"""Autenticación de la webapp: OTP por WhatsApp + sesiones (rama webapp).

El dueño del número recibe un código de un solo uso por WhatsApp (el MISMO
canal del producto) y con él canjea una sesión. Nadie ve datos de un teléfono
sin demostrar que lo controla.

Políticas (OWASP MFA cheat sheet / NIST 800-63B):
- Código de 6 dígitos generado con `secrets` (CSPRNG), TTL corto (5 min).
- En reposo SOLO el hash (sha256 con el teléfono como contexto) — el código
  en claro únicamente viaja por WhatsApp.
- Un solo uso; máximo 5 intentos de verificación por código; comparación en
  tiempo constante (hmac.compare_digest).
- Cooldown de reenvío (60 s) por teléfono — anti spam/costo de mensajería.
- La respuesta de solicitud no revela si el número tiene cuenta.
- Token de sesión de 256 bits; en la base vive su hash; expira a los 7 días.

`AUTH_DEMO_OTP` (opcional, vacío = apagado): código maestro para demos ante
jurado cuando el número no está unido al sandbox de Twilio. Documentado como
tal — jamás activarlo en producción.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.models import AuthCode, Session, User
from app.domain.ports import ChannelAdapter, Repository

log = logging.getLogger("e5.auth")

OTP_DIGITOS = 6
OTP_TTL_MIN = 5
OTP_MAX_INTENTOS = 5
REENVIO_COOLDOWN_S = 60
SESSION_TTL_DIAS = 7

MENSAJE_OTP = (
    "🔐 Tu código para entrar a Luca es *{codigo}*. "
    "Caduca en {ttl} minutos y solo sirve una vez. "
    "Si no intentaste entrar, ignora este mensaje — nunca lo compartas."
)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _hash_codigo(telefono: str, codigo: str) -> str:
    # El teléfono como contexto liga el código a SU número (no intercambiable).
    return hashlib.sha256(f"{telefono}:{codigo}".encode()).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class CooldownActivo(Exception):
    def __init__(self, segundos: int) -> None:
        self.segundos = segundos


class AuthService:
    def __init__(
        self,
        repo: Repository,
        channel: ChannelAdapter,
        demo_otp: str = "",
    ) -> None:
        self._repo = repo
        self._channel = channel
        self._demo_otp = demo_otp

    # ------------------------------------------------------------- solicitar
    async def solicitar_codigo(self, telefono: str) -> None:
        """Genera y envía el OTP por WhatsApp. Lanza CooldownActivo si se pide
        de nuevo demasiado pronto. No revela si el número tiene cuenta."""
        activo = self._repo.get_auth_code_activo(telefono)
        if activo is not None:
            transcurrido = (_ahora() - activo.created_at).total_seconds()
            if transcurrido < REENVIO_COOLDOWN_S:
                raise CooldownActivo(int(REENVIO_COOLDOWN_S - transcurrido))

        codigo = "".join(str(secrets.randbelow(10)) for _ in range(OTP_DIGITOS))
        self._repo.save_auth_code(
            AuthCode(
                telefono=telefono,
                codigo_hash=_hash_codigo(telefono, codigo),
                expira_at=_ahora() + timedelta(minutes=OTP_TTL_MIN),
            )
        )
        user = self._repo.get_or_create_user(telefono)
        try:
            await self._channel.send(
                user, MENSAJE_OTP.format(codigo=codigo, ttl=OTP_TTL_MIN)
            )
        except Exception:
            # No filtrar el código al log. El usuario puede reintentar tras el
            # cooldown; en demo existe AUTH_DEMO_OTP como plan B documentado.
            log.exception("No se pudo entregar el OTP por WhatsApp a %s", telefono)

    # -------------------------------------------------------------- verificar
    async def verificar_codigo(self, telefono: str, codigo: str) -> Optional[str]:
        """Devuelve el token de sesión (en claro, para el cliente) si el código
        es válido; None si no. El token solo se persiste hasheado."""
        codigo = codigo.strip()

        # Código maestro de demo (apagado por defecto; ver docstring del módulo).
        if self._demo_otp and hmac.compare_digest(codigo, self._demo_otp):
            return self._emitir_sesion(telefono)

        activo = self._repo.get_auth_code_activo(telefono)
        if activo is None or activo.expira_at < _ahora():
            return None
        if activo.intentos >= OTP_MAX_INTENTOS:
            return None

        if not hmac.compare_digest(activo.codigo_hash, _hash_codigo(telefono, codigo)):
            self._repo.incrementar_intentos_codigo(activo.id)
            return None

        self._repo.marcar_codigo_usado(activo.id)  # un solo uso
        return self._emitir_sesion(telefono)

    # ---------------------------------------------------------------- sesión
    def _emitir_sesion(self, telefono: str) -> str:
        user = self._repo.get_or_create_user(telefono)
        token = secrets.token_urlsafe(32)  # 256 bits
        self._repo.create_session(
            Session(
                token_hash=_hash_token(token),
                user_id=user.id,
                expira_at=_ahora() + timedelta(days=SESSION_TTL_DIAS),
            )
        )
        return token

    def usuario_de_token(self, token: str) -> Optional[User]:
        """Resuelve la sesión Bearer → User, o None si no existe/expiró."""
        if not token:
            return None
        session = self._repo.get_session(_hash_token(token))
        if session is None or session.expira_at < _ahora():
            return None
        return self._repo.get_user(session.user_id)

    def cerrar_sesion(self, token: str) -> None:
        if token:
            self._repo.delete_session(_hash_token(token))
