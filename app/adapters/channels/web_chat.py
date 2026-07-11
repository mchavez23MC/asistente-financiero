"""ChannelAdapter de chat web (plan B, §9) — Fase 9.

Segundo adaptador del mismo puerto `ChannelAdapter` — la prueba viviente del
argumento de extensibilidad (§7.1) y el respaldo si Meta/Fly.io fallan en vivo
(§9). A diferencia de WhatsApp (respuesta out-of-band por la Graph API de Meta), el
web chat es request/response: este canal CAPTURA los envíos para devolverlos en
la misma respuesta HTTP.
"""

from __future__ import annotations

from app.domain.models import IncomingMessage, User


class WebChatCapturingChannel:
    """Recoge lo que el orquestador 'envía' para responderlo en banda."""

    canal = "web"

    def __init__(self, telefono: str) -> None:
        self._telefono = telefono
        self.enviados: list[str] = []

    def parse(self, payload: dict) -> IncomingMessage:
        return IncomingMessage(
            canal=self.canal,
            telefono=self._telefono,
            texto=str(payload.get("texto", "")).strip(),
            nombre_perfil=payload.get("nombre") or "Web",
        )

    async def send(self, user: User, text: str) -> None:
        self.enviados.append(text)
