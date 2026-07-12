"""ChannelAdapter sobre WhatsApp de Twilio (§7.1) — canal ACTIVO.

Es el canal que usa la app hoy. El núcleo no cambia (misma interfaz
`ChannelAdapter` que el adaptador de Meta): solo cambia cómo se parsea el
webhook y cómo se envía la respuesta.

Diferencias frente a Meta (ver `whatsapp_meta.py`, que se conserva como ejemplo
para empresas con acceso directo a la Cloud API):
- Entrada: Twilio postea `application/x-www-form-urlencoded` (NO JSON). Campos
  relevantes: `From` (`whatsapp:+503...`), `Body`, `ProfileName`, `WaId`.
- Salida: REST API de Twilio (`POST /Accounts/{sid}/Messages.json`) con Basic
  Auth (AccountSid:AuthToken), no Bearer token.
- Firma: `X-Twilio-Signature` = base64(HMAC-SHA1(auth_token, url + params
  ordenados)); distinta al `X-Hub-Signature-256` de Meta.
- Ventana de 24h de WhatsApp: igual que Meta, fuera de ella solo plantillas
  aprobadas (en el sandbox de Twilio no aplica para números ya unidos).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx

from app.domain.models import IncomingMessage, User

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

log = logging.getLogger("e5.whatsapp.twilio")


class WhatsAppTwilioAdapter:
    canal = "whatsapp"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        whatsapp_from: str,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        # Número emisor en formato Twilio ('whatsapp:+14155238886').
        self._from = self._to_whatsapp(whatsapp_from)
        self._url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
        # Cliente reutilizable: evita un handshake TLS nuevo por cada envío
        # (~100-300 ms). Se crea perezosamente en el primer send (así los
        # adaptadores que nunca envían —p.ej. en tests— no abren conexiones).
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                auth=(self._account_sid, self._auth_token), timeout=15
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def parse(self, payload: dict) -> IncomingMessage:
        """Normaliza el webhook (form-encoded → dict) de Twilio al formato
        canónico. Si no hay cuerpo de texto, devuelve un mensaje vacío que el
        webhook ignora (p.ej. eventos de status/entrega)."""
        remitente = str(payload.get("From", ""))
        telefono = self._from_whatsapp(remitente)
        texto = str(payload.get("Body", "") or "").strip()
        nombre = payload.get("ProfileName") or None

        if not telefono or not texto:
            return IncomingMessage(canal=self.canal, telefono="", texto="", raw=payload)

        return IncomingMessage(
            canal=self.canal,
            telefono=telefono,
            texto=texto,
            nombre_perfil=nombre,
            raw=payload,
        )

    async def send(self, user: User, text: str) -> None:
        to = self._to_whatsapp(user.telefono)
        resp = await self._get_client().post(
            self._url,
            data={"From": self._from, "To": to, "Body": text},
        )
        if resp.is_error:
            # El cuerpo de Twilio trae `code` y `message` con el motivo exacto
            # (ej. 63016 fuera de la ventana de 24h, 21608 número no unido al
            # sandbox). Se registra y se propaga para que el orquestador lo trate.
            log.error(
                "Twilio API devolvió %s al enviar a %s: %s",
                resp.status_code,
                to,
                resp.text,
            )
            raise httpx.HTTPStatusError(
                f"Twilio API {resp.status_code}: {resp.text}",
                request=resp.request,
                response=resp,
            )

    # ------------------------------------------------------------------ firma
    @staticmethod
    def firma_valida(url: str, params: dict, firma: str, auth_token: str) -> bool:
        """Valida `X-Twilio-Signature` (HMAC-SHA1). El string firmado es la URL
        pública EXACTA que Twilio invocó, concatenada con cada par
        clave+valor de los params POST ordenados por clave. Debe coincidir con el
        `Callback URL` configurado en Twilio (incluye esquema y host)."""
        cadena = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        esperado = base64.b64encode(
            hmac.new(auth_token.encode(), cadena.encode("utf-8"), hashlib.sha1).digest()
        ).decode()
        return hmac.compare_digest(esperado, firma or "")

    # ------------------------------------------------------------------ internos
    @staticmethod
    def _to_whatsapp(numero: str) -> str:
        """E.164 ('+503...') → formato Twilio ('whatsapp:+503...')."""
        numero = numero.strip()
        if numero.startswith("whatsapp:"):
            return numero
        if numero and not numero.startswith("+"):
            numero = "+" + numero
        return f"whatsapp:{numero}"

    @staticmethod
    def _from_whatsapp(remitente: str) -> str:
        """Formato Twilio ('whatsapp:+503...') → E.164 ('+503...')."""
        numero = remitente.strip()
        if numero.startswith("whatsapp:"):
            numero = numero[len("whatsapp:") :]
        if numero and not numero.startswith("+"):
            numero = "+" + numero
        return numero
