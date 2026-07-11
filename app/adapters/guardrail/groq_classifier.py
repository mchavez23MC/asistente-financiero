"""Clasificador de sensibilidad sobre Groq (GPT-OSS-20B) — Fase 3 (§1.3).

JSON mode con el schema {"sensible", "categoria", "confianza"}. Este módulo
solo clasifica; el timeout, el umbral y el fail-closed viven en
`LayeredGuardrail` (layered.py). Si falsos negativos en fase 8 → escalar a
GPT-OSS-120B (decisión ya tomada, §1.3: cambiar GROQ_MODEL, no rediseñar).
"""

from __future__ import annotations

import json

from groq import AsyncGroq

from app.domain.models import GuardrailResult

SYSTEM_PROMPT = """\
Eres un clasificador de riesgo para un asistente financiero por WhatsApp.
Clasifica el mensaje del usuario como sensible o no. Es SENSIBLE si pide o
implica: consejo de inversión (dónde/en qué invertir, cripto, acciones,
rendimientos), un reclamo o queja formal, sospecha de fraude o cargos no
reconocidos, o temas regulatorios/legales. NO es sensible: registrar gastos,
consultar su presupuesto o saldo, preguntas de soporte sobre el servicio,
saludos y charla cotidiana.

Responde SOLO con JSON válido, sin texto adicional:
{"sensible": true|false, "categoria": "consejo_inversion"|"reclamo"|"fraude"|"regulatorio"|"otro"|"ninguna", "confianza": número entre 0 y 1}
"confianza" es tu certeza en la clasificación (no la probabilidad de que sea sensible)."""


class GroqClassifier:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def classify(self, texto: str) -> GuardrailResult:
        """Una inferencia. Cualquier excepción o JSON inválido la maneja
        LayeredGuardrail como fail-closed — aquí no se atrapa nada a propósito."""
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": texto},
            ],
            response_format={"type": "json_object"},
            reasoning_effort="low",
            temperature=0,
            max_tokens=1024,
        )
        data = json.loads(resp.choices[0].message.content)
        return GuardrailResult(
            sensible=bool(data["sensible"]),
            categoria=str(data.get("categoria", "otro")),
            confianza=float(data.get("confianza", 0.0)),
            fuente="clasificador",
        )
