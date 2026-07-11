"""Capa 1 del guardrail: denylist determinística (§1.4 / §7.3) — Fase 3.

Match → sensible=True sin llamar a ningún modelo: costo cero, latencia cero,
imposible de jailbreakear. Se amplía en la fase 8 con lo que se le escape al
clasificador durante la prueba de fuego.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# categoria (valores de MotivoEscalacion) → términos/frases de alto riesgo.
# El match es por palabra completa sobre texto normalizado (minúsculas, sin tildes).
DENYLIST: dict[str, tuple[str, ...]] = {
    "consejo_inversion": (
        "invertir", "inversion", "inversiones", "cripto", "criptomoneda",
        "criptomonedas", "bitcoin", "acciones", "bolsa de valores", "trading",
        "forex", "rendimiento garantizado", "plazo fijo",
    ),
    "fraude": (
        "fraude", "estafa", "estafaron", "estafar", "robo", "robaron",
        "hackearon", "hackeo", "clonaron", "phishing", "cargo no reconocido",
    ),
    "reclamo": (
        "reclamo", "queja", "demanda", "demandar", "abogado", "denuncia",
        "reembolso", "devolucion del dinero",
    ),
    "regulatorio": (
        "lavado de dinero", "defensoria del consumidor", "superintendencia",
    ),
}


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para que 'Inversión' matchee 'inversion'."""
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Compilado una sola vez: categoria → regex con todos sus términos.
_PATRONES: dict[str, re.Pattern] = {
    categoria: re.compile(
        r"\b(" + "|".join(re.escape(t) for t in terminos) + r")\b"
    )
    for categoria, terminos in DENYLIST.items()
}


def match_denylist(texto: str) -> Optional[str]:
    """Devuelve la categoría del primer término que matchee, o None."""
    normalizado = _normalizar(texto)
    for categoria, patron in _PATRONES.items():
        if patron.search(normalizado):
            return categoria
    return None
