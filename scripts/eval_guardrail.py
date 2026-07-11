"""Prueba de fuego del guardrail (§8.3) — Fase 8.

Dispara el set de 40+ frases contra el guardrail REAL (denylist → Groq → umbral)
y reporta:
  - recall de la clase 'sensible' (el número que importa, §1.4),
  - desglose por capa que atrapó cada sensible (denylist / clasificador / umbral),
  - falsos negativos en 'sensible' (si los hay → calibrar umbral / ampliar denylist
    o escalar a GPT-OSS-120B, §1.3),
  - batería de ataques.

Uso:  uv run python -m scripts.eval_guardrail
Requiere GROQ_API_KEY real en el entorno. No toca Anthropic ni Supabase.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter

# Throttle entre llamadas para no chocar con el rate-limit del tier gratuito de
# Groq (§1.3). Ajustable con EVAL_THROTTLE_MS.
_THROTTLE = float(os.environ.get("EVAL_THROTTLE_MS", "400")) / 1000.0

from app.adapters.guardrail.groq_classifier import GroqClassifier
from app.adapters.guardrail.layered import LayeredGuardrail
from app.infra.config import Settings
from scripts.frases_eval import ATAQUES, SENSIBLES, TODAS


async def main() -> None:
    s = Settings.from_env()
    g = LayeredGuardrail(
        classifier=GroqClassifier(s.groq_api_key, s.groq_model),
        umbral_confianza=s.guardrail_umbral_confianza,
        timeout_ms=s.guardrail_timeout_ms,
    )

    print(f"Umbral={s.guardrail_umbral_confianza}  timeout={s.guardrail_timeout_ms}ms  modelo={s.groq_model}\n")

    fuentes = Counter()
    falsos_negativos, falsos_positivos = [], []
    for f in TODAS:
        r = await g.classify(f.texto)
        await asyncio.sleep(_THROTTLE)
        if f.sensible and not r.sensible:
            falsos_negativos.append(f.texto)
        elif not f.sensible and r.sensible:
            falsos_positivos.append((f.texto, r.fuente))
        if f.sensible and r.sensible:
            fuentes[r.fuente] += 1

    n_sensibles = len(SENSIBLES)
    atrapados = n_sensibles - len(falsos_negativos)
    recall = atrapados / n_sensibles if n_sensibles else 1.0

    print(f"=== Clase 'sensible' ({n_sensibles} frases) ===")
    print(f"Recall: {recall:.0%}  ({atrapados}/{n_sensibles})")
    print(f"Atrapadas por capa: {dict(fuentes)}")
    if falsos_negativos:
        print("\n⚠️  FALSOS NEGATIVOS (revisar denylist / umbral / escalar a 120B):")
        for t in falsos_negativos:
            print(f"   - {t!r}")
    else:
        print("✅ Cero falsos negativos en 'sensible'.")

    if falsos_positivos:
        print(f"\nFalsos positivos ({len(falsos_positivos)}) — no bloquean, pero escalan de más:")
        for t, fuente in falsos_positivos:
            print(f"   - [{fuente}] {t!r}")

    print("\n=== Batería de ataques (todos deberían escalar) ===")
    for texto in ATAQUES:
        r = await g.classify(texto)
        await asyncio.sleep(_THROTTLE)
        marca = "✅" if r.sensible else "❌ PASÓ"
        print(f"   {marca}  [{r.fuente}] {texto[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
