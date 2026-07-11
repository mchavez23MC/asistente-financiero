"""Guardrail en capas, fail-closed (§1.4 / §7.3.4) — Fase 3.

Orden: denylist (capa 1, determinística) → clasificador Groq → umbral de
confianza (capa 2). Timeout o error del clasificador → el mensaje se trata
como potencialmente sensible; NUNCA fluye sin clasificar.

El umbral (0.7 provisional) y la denylist se calibran en la fase 8 contra
las 40+ frases de T2.
"""

from __future__ import annotations

import asyncio

from app.domain.models import GuardrailResult
from app.domain.ports import Guardrail
from app.adapters.guardrail.denylist import match_denylist


class LayeredGuardrail:
    def __init__(
        self,
        classifier: Guardrail,
        umbral_confianza: float = 0.7,
        timeout_ms: int = 800,
    ) -> None:
        self._classifier = classifier
        self._umbral = umbral_confianza
        self._timeout = timeout_ms / 1000.0

    async def classify(self, texto: str) -> GuardrailResult:
        # Capa 1: denylist. Match → sensible sin inferencia.
        categoria = match_denylist(texto)
        if categoria is not None:
            return GuardrailResult(
                sensible=True, categoria=categoria, confianza=1.0, fuente="denylist"
            )

        # Clasificador Groq, acotado por timeout. Fail-closed ante todo (§7.3.4).
        try:
            resultado = await asyncio.wait_for(
                self._classifier.classify(texto), timeout=self._timeout
            )
        except Exception:
            return GuardrailResult(
                sensible=True, categoria="desconocida", confianza=0.0, fuente="fail_closed"
            )

        # Capa 2: baja confianza → se fuerza sensible, sin nueva inferencia.
        if resultado.confianza < self._umbral:
            return GuardrailResult(
                sensible=True,
                categoria=resultado.categoria,
                confianza=resultado.confianza,
                fuente="umbral",
            )
        return resultado
