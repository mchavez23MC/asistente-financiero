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

from app.domain.models import IncomingMessage, MediaItem, User

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

log = logging.getLogger("e5.whatsapp.twilio")


class WhatsAppTwilioAdapter:
    canal = "whatsapp"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        whatsapp_from: str,
        descargar_extendido: bool = False,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        # Con el flujo de documentos activo (DOCS_HABILITADO) también se
        # descargan XML/CSV/XLSX (item.descargable); apagado, solo lo que el
        # modelo puede ver (item.soportado) — no se baja nada en vano (R4).
        self._descargar_extendido = descargar_extendido
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
        canónico. Un mensaje con imagen/documento (NumMedia > 0) es válido
        aunque no traiga Body (foto de recibo sin caption). Si no hay ni texto
        ni media, devuelve un mensaje vacío que el webhook ignora (p.ej.
        eventos de status/entrega)."""
        remitente = str(payload.get("From", ""))
        telefono = self._from_whatsapp(remitente)
        texto = str(payload.get("Body", "") or "").strip()
        nombre = payload.get("ProfileName") or None
        media = self._parse_media(payload)

        if not telefono or (not texto and not media):
            return IncomingMessage(canal=self.canal, telefono="", texto="", raw=payload)

        return IncomingMessage(
            canal=self.canal,
            telefono=telefono,
            texto=texto,
            nombre_perfil=nombre,
            media=media,
            raw=payload,
        )

    @staticmethod
    def _parse_media(payload: dict) -> list[MediaItem]:
        """Twilio adjunta `NumMedia` y pares `MediaUrl{i}`/`MediaContentType{i}`.
        Solo se capturan url + content-type; la descarga (requiere Basic Auth de
        la cuenta) la hace `fetch_media` en background, después del 200."""
        try:
            num = int(payload.get("NumMedia", 0) or 0)
        except (TypeError, ValueError):
            num = 0
        items: list[MediaItem] = []
        for i in range(num):
            url = payload.get(f"MediaUrl{i}")
            if not url:
                continue
            items.append(
                MediaItem(
                    url=str(url),
                    content_type=str(payload.get(f"MediaContentType{i}", "") or "").strip()
                    or "application/octet-stream",
                )
            )
        return items

    async def fetch_media(self, incoming: IncomingMessage) -> None:
        """Descarga los adjuntos soportados del mensaje (in place, llena
        `data_base64`). Las URLs de media de Twilio requieren la misma Basic
        Auth que la API de envío, por eso vive en el adaptador y no en el core.
        Un adjunto que falla o excede el límite queda con data_base64=None y el
        agente se lo dice al usuario — nunca tira el pipeline completo."""
        for item in incoming.media:
            permitido = item.descargable if self._descargar_extendido else item.soportado
            if not item.url or not permitido or item.data_base64 is not None:
                continue
            try:
                resp = await self._get_client().get(item.url, follow_redirects=True)
                resp.raise_for_status()
                limite = item.limite_bytes
                if len(resp.content) > limite:
                    log.warning(
                        "Adjunto %s de %s excede el límite (%d bytes); se omite.",
                        item.content_type,
                        incoming.telefono,
                        len(resp.content),
                    )
                    continue
                item.data_base64 = base64.b64encode(resp.content).decode("ascii")
            except Exception:
                log.exception("No se pudo descargar el adjunto %s", item.url)

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
