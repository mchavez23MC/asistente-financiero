"""Reglas de escalación a humano (tickets) — compartidas entre el orquestador
(`process_message`) y el agente principal (`principal`).

Dos reglas de negocio viven aquí para no duplicarlas ni desincronizarlas:

1. `pide_humano`: detección determinística de un pedido EXPLÍCITO de hablar con
   una persona. Es lo único que autoriza a crear un ticket — Luca nunca escala
   por su cuenta (§comportamiento). Un mensaje sensible (inversión, fraude,
   reclamo) se DECLINA salvo que además pida un humano expresamente.

2. `en_cooldown`: límite de 1 ticket cada `TICKET_COOLDOWN` por usuario, para no
   inundar la cola humana (y evitar el 429/too-many-requests aguas abajo).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

# Un ticket cada 5 horas por usuario, como máximo.
TICKET_COOLDOWN = timedelta(hours=5)


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para que 'conéctame' matchee 'conectame'."""
    nfkd = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Frases que expresan un pedido explícito de atención humana. El match es laxo
# (substring sobre texto normalizado) porque son expresiones inequívocas: un
# falso positivo solo crea un ticket que el usuario de hecho pidió.
_FRASES_HUMANO: tuple[str, ...] = (
    "hablar con una persona",
    "hablar con alguien",
    "hablar con un humano",
    "hablar con un agente",
    "hablar con un representante",
    "hablar con un asesor",
    "hablar con soporte",
    "con una persona real",
    "una persona real",
    "un humano real",
    "atencion al cliente",
    "servicio al cliente",
    "agente humano",
    "quiero un humano",
    "necesito un humano",
    "conectame con",
    "conectame a",
    "pasame con",
    "pasame a",
    "comunicame con",
    "quiero hablar con alguien",
    "que me ayude una persona",
    "que me atienda una persona",
    "crear un ticket",
    "crea un ticket",
    "abrir un ticket",
    "abre un ticket",
    "levantar un ticket",
)


def pide_humano(texto: str) -> bool:
    """True si el mensaje pide explícitamente hablar con una persona real / crear
    un ticket. Es la única señal que autoriza una escalación."""
    normalizado = _normalizar(texto)
    return any(frase in normalizado for frase in _FRASES_HUMANO)


def en_cooldown(ultimo_ticket_at: Optional[datetime], ahora: Optional[datetime] = None) -> bool:
    """True si el usuario creó un ticket hace menos de `TICKET_COOLDOWN`. `None`
    (sin tickets previos) nunca está en cooldown. Normaliza fechas naive a UTC."""
    if ultimo_ticket_at is None:
        return False
    ahora = ahora or datetime.now(timezone.utc)
    if ultimo_ticket_at.tzinfo is None:
        ultimo_ticket_at = ultimo_ticket_at.replace(tzinfo=timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    return (ahora - ultimo_ticket_at) < TICKET_COOLDOWN
