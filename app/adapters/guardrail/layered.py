"""Guardrail en capas, fail-closed (§1.4 / §7.3.4) — Fase 3.

Orden: denylist (capa 1, determinística) → clasificador Groq → umbral de
confianza (capa 2). Timeout o error del clasificador → el mensaje se trata
como potencialmente sensible; NUNCA fluye sin clasificar.

Resiliencia (calibrado en fase 8): el tier gratuito de Groq throttlea bajo
ráfagas (429) y a veces responde lento. Para no escalar TODO a ticket ante un
Groq intermitente, se reintenta el clasificador unas veces con backoff corto
antes de fallar cerrado — sin renunciar a la garantía fail-closed: si de verdad
está caído, igual escala. El presupuesto total queda acotado porque el guardrail
corre síncrono antes del 200 del webhook (§7.5).
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
        timeout_ms: int = 1200,
        reintentos: int = 1,
        backoff_ms: int = 300,
    ) -> None:
        self._classifier = classifier
        self._umbral = umbral_confianza
        self._timeout = timeout_ms / 1000.0
        self._reintentos = max(0, reintentos)
        self._backoff = backoff_ms / 1000.0

    async def classify(self, texto: str) -> GuardrailResult:
        # Capa 1: denylist. Match → sensible sin inferencia.
        categoria = match_denylist(texto)
        if categoria is not None:
            return GuardrailResult(
                sensible=True, categoria=categoria, confianza=1.0, fuente="denylist"
            )

        # Clasificador Groq con timeout por intento y reintentos ante fallo
        # transitorio (timeout / 429 / error de red). Fail-closed al agotarlos.
        fuente_fallo = "fail_closed_error"
        for intento in range(self._reintentos + 1):
            try:
                resultado = await asyncio.wait_for(
                    self._classifier.classify(texto), timeout=self._timeout
                )
            except asyncio.TimeoutError:
                fuente_fallo = "fail_closed_timeout"
            except Exception:
                fuente_fallo = "fail_closed_error"
            else:
                # Capa 2: baja confianza → se fuerza sensible, sin nueva inferencia.
                if resultado.confianza < self._umbral:
                    return GuardrailResult(
                        sensible=True,
                        categoria=resultado.categoria,
                        confianza=resultado.confianza,
                        fuente="umbral",
                    )
                return resultado

            if intento < self._reintentos:
                await asyncio.sleep(self._backoff)

        # Se agotaron los intentos: fail-closed (§7.3.4).
        return GuardrailResult(
            sensible=True, categoria="desconocida", confianza=0.0, fuente=fuente_fallo
        )
