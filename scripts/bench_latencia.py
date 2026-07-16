"""Banco de pruebas de latencia por componente (diagnóstico).

Mide, contra los servicios REALES (usa las claves de .env), cuánto tarda cada
pieza del pipeline por separado, para ubicar el cuello de botella:

  1. Guardrail (Groq)      — una clasificación de sensibilidad.
  2. Claude, 1 turno       — una sola inferencia (elegir tool / responder).
  3. Claude, caché frío/caliente — impacto del prompt caching en el TTFT.
  4. Supabase (lecturas)   — get_last_n_messages, list_transactions, sum_gastos.
  5. End-to-end simulado   — nº de turnos de Claude para un mensaje simple.

Por defecto NO escribe en la base de datos. Con --write ejecuta un
registrar_gasto real (y lo deja como transacción de prueba; bórrala luego).

Uso:
    python -m scripts.bench_latencia            # solo lecturas + Claude/Groq
    python -m scripts.bench_latencia --write     # incluye un registro real
    python -m scripts.bench_latencia --repeticiones 3

Cada llamada a Claude/Groq CUESTA dinero (poco). Es un diagnóstico puntual.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from uuid import uuid4

from app.adapters.guardrail.groq_classifier import GroqClassifier
from app.adapters.guardrail.layered import LayeredGuardrail
from app.adapters.llm.claude import ClaudeProvider
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.agents.principal import _system_blocks
from app.domain.tools import TOOLS
from app.infra.config import Settings


def _cronometro():
    inicio = time.perf_counter()
    return lambda: (time.perf_counter() - inicio) * 1000  # ms


def _resumen(nombre: str, muestras: list[float]) -> None:
    if not muestras:
        print(f"  {nombre:<38} sin muestras")
        return
    media = statistics.mean(muestras)
    p_min, p_max = min(muestras), max(muestras)
    print(f"  {nombre:<38} media {media:7.0f}ms   (min {p_min:.0f} / max {p_max:.0f})   n={len(muestras)}")


async def bench_guardrail(settings: Settings, n: int) -> list[float]:
    guardrail = LayeredGuardrail(
        classifier=GroqClassifier(settings.groq_api_key, settings.groq_model),
        umbral_confianza=settings.guardrail_umbral_confianza,
        timeout_ms=settings.guardrail_timeout_ms,
        reintentos=settings.guardrail_reintentos,
        backoff_ms=settings.guardrail_backoff_ms,
    )
    muestras = []
    for _ in range(n):
        t = _cronometro()
        await guardrail.classify("gasté 25 en el almuerzo")
        muestras.append(t())
    return muestras


async def bench_claude_un_turno(settings: Settings, n: int) -> tuple[list[float], list[float]]:
    """Devuelve (frío, caliente): la 1ª llamada no tiene el prefijo en caché; las
    siguientes sí (mismo system+tools). Mide el impacto del prompt caching."""
    claude = ClaudeProvider(
        settings.anthropic_api_key, settings.claude_model, settings.claude_max_tokens
    )
    system = _system_blocks()
    mensajes = [{"role": "user", "content": "gasté 25 en el almuerzo"}]
    frio, caliente = [], []
    for i in range(n + 1):
        t = _cronometro()
        resp = await claude.complete(messages=mensajes, tools=TOOLS, system=system)
        dt = t()
        (frio if i == 0 else caliente).append(dt)
        cache = resp.cache_read_tokens or 0
        print(f"    turno {i}: {dt:.0f}ms   in={resp.input_tokens} cache_read={cache} out={resp.output_tokens} stop={resp.stop_reason}")
    return frio, caliente


def bench_supabase_lecturas(settings: Settings, n: int) -> dict[str, list[float]]:
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)
    # Un usuario cualquiera existente para lecturas realistas; si no hay, se usa
    # uno aleatorio (las consultas devuelven vacío pero el round-trip se mide).
    recientes = repo.recent_messages(1)
    user_id = recientes[0].user_id if recientes else uuid4()
    print(f"    (midiendo contra user_id={user_id})")
    resultados = {"get_last_n_messages(10)": [], "list_transactions(5)": [], "sum_gastos(mensual)": []}
    for _ in range(n):
        t = _cronometro(); repo.get_last_n_messages(user_id, 10); resultados["get_last_n_messages(10)"].append(t())
        t = _cronometro(); repo.list_transactions(user_id, 5); resultados["list_transactions(5)"].append(t())
        t = _cronometro(); repo.sum_gastos(user_id, periodo="mensual"); resultados["sum_gastos(mensual)"].append(t())
    return resultados


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--write", action="store_true", help="incluye un registrar_gasto real")
    args = parser.parse_args()
    settings = Settings.from_env()
    n = args.repeticiones

    print(f"\n=== Benchmark de latencia (modelo agente: {settings.claude_model}) ===\n")

    print("[1] Guardrail (Groq):")
    _resumen("guardrail.classify", await bench_guardrail(settings, n))

    print("\n[2/3] Claude — una inferencia (con tools + system cacheado):")
    frio, caliente = await bench_claude_un_turno(settings, n)
    _resumen("Claude 1er turno (caché FRÍA)", frio)
    _resumen("Claude turnos siguientes (caché CALIENTE)", caliente)

    print("\n[4] Supabase — lecturas (round-trip REST):")
    for nombre, muestras in bench_supabase_lecturas(settings, n).items():
        _resumen(nombre, muestras)

    if args.write:
        from app.application.agents.gasto import registrar_gasto
        repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)
        recientes = repo.recent_messages(1)
        user_id = recientes[0].user_id if recientes else None
        if user_id:
            print("\n[5] registrar_gasto (ESCRIBE en la DB — borra la tx de prueba luego):")
            t = _cronometro()
            out = registrar_gasto(repo, user_id, monto=1.11, categoria="benchmark", comercio="bench")
            print(f"    registrar_gasto completo: {t():.0f}ms  → {out}")

    print("\n=== Lectura del resultado ===")
    print("La latencia percibida ≈ guardrail + (Nº turnos × Claude) + DB + envío.")
    print("Un registro simple = 2 turnos de Claude (elegir tool + confirmar).")
    print("Si 'Claude por turno' domina, el cuello es el modelo/round-trips, no la DB.\n")


if __name__ == "__main__":
    asyncio.run(main())
