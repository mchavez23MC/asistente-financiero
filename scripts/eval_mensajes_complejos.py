"""Evaluación con mensajes complejos y realistas (tipo mensaje de voz).

Las personas al hablar (sobre todo por voz) divagan, dan datos innecesarios,
repiten lo mismo de varias formas, se autocorrigen y meten varias cosas en una
sola frase. Este script dispara ese tipo de mensajes contra el agente REAL
(Haiku, según .env) con FakeRepo (no toca Supabase) y reporta, por mensaje:

  - latencia (mediana de N repeticiones),
  - nº de turnos de Claude por repetición,
  - qué terminó registrando (transacciones en el repo) y su estado,
  - la respuesta al usuario.

Es una evaluación de comportamiento + latencia, NO un test determinista: usa el
modelo real, así que los resultados pueden variar entre corridas. Para los tests
deterministas del código ver tests/test_agente.py.

Uso:  python -m scripts.eval_mensajes_complejos [--reps N]
Requiere ANTHROPIC_API_KEY real. Cuesta unos centavos por corrida.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from app.adapters.llm.claude import ClaudeProvider
from app.application.agents.principal import MainAgent
from app.domain.models import AgentContext, IncomingMessage
from app.infra.config import Settings
from tests.test_agente import FakeSoporte
from tests.test_walking_skeleton import FakeRepo

# Mensajes tipo voz: relleno, redundancia, autocorrección, varias cosas juntas.
CASOS = [
    (
        "Multi-gasto con relleno (backstory largo)",
        "ve Luca buenos días, mira hoy me fui de vueltas con mi ñaña todo el día, "
        "primero paramos a desayunar en un café de la esquina que estuvo rico, fueron "
        "como 8 dólares, después nos caímos al Supermaxi y ahí sí gasté full, unos 45 "
        "en cosas para la casa, ah y casi me olvido, el Uber de ida fueron 6 con 50",
        "Espera 3 gastos: ~8 café, ~45 supermercado, ~6.50 transporte.",
    ),
    (
        "Autocorrección a media frase",
        "anótame un gasto de 20 en el almuerzo de hoy... no verás, mentira, fueron 25 "
        "no 20, es que me confundí con el vuelto",
        "Espera 1 gasto de 25 (no 20).",
    ),
    (
        "Redundante: dice lo mismo de varias formas",
        "oye necesito que me anotes una compra, un gastito que hice, o sea pagué algo, "
        "fueron unos 30 dolaritos en la farmacia por unas medicinas",
        "Espera 1 gasto de 30 en farmacia/salud.",
    ),
    (
        "Ingreso + gasto en una sola frase conversacional",
        "uf por fin me pagaron el sueldo hermano, 500 dolaritos que buena falta me "
        "hacían, pero ojo que ya empecé a gastar, boté 30 esta mañana en el mercado",
        "Espera 1 ingreso de 500 y 1 gasto de 30.",
    ),
    (
        "Relleno + registro + pregunta (mixto)",
        "buenos días Luca, ayer domingo salí con toda la familia a pasear, gasté 60 en "
        "el súper grande porque tocaba llenar la despensa, y de paso aprovecho y "
        "pregúntote cómo voy con mi presupuesto de comida este mes que siento que me pasé",
        "Espera registrar_gasto 60 + consultar_presupuesto (mixto → 2 turnos).",
    ),
    (
        "Vago / dato faltante (no debe inventar)",
        "uy Luca gasté un montón hoy en el súper, en serio full plata, pero ni me "
        "acuerdo cuánto exactamente la verdad",
        "Espera que PREGUNTE el monto (queda pendiente), sin inventar cifra.",
    ),
]


class CuentaLlamadas(ClaudeProvider):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.n = 0

    async def complete(self, **k):
        self.n += 1
        return await super().complete(**k)


def _fmt_tx(repo: FakeRepo) -> str:
    if not repo.transactions:
        return "     (ninguna transacción registrada)"
    lineas = []
    for t in repo.transactions:
        tipo = t.tipo if isinstance(t.tipo, str) else t.tipo.value
        status = t.status if isinstance(t.status, str) else t.status.value
        monto = f"${t.monto}" if t.monto is not None else "sin monto"
        lineas.append(f"     - {tipo}: {monto} · {t.categoria or '?'} · {t.comercio or '-'} [{status}]")
    return "\n".join(lineas)


async def _una_corrida(llm, texto):
    repo = FakeRepo()
    agente = MainAgent(llm=llm, repo=repo, soporte=FakeSoporte(
        {"encontrado_en_corpus": False, "respuesta": "", "cita": None}))
    user = repo.get_or_create_user("+50370000000", "Ana")
    ctx = AgentContext(user=user, incoming=IncomingMessage(canal="web", telefono=user.telefono, texto=texto), historial=[])
    llm.n = 0
    t = time.perf_counter()
    r = await agente.handle(ctx)
    dt = (time.perf_counter() - t) * 1000
    return dt, llm.n, repo, r.respuesta


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    args = ap.parse_args()

    s = Settings.from_env()
    llm = CuentaLlamadas(s.anthropic_api_key, s.claude_model, s.claude_max_tokens)
    print(f"\n=== Evaluación de mensajes complejos (modelo: {s.claude_model}, reps: {args.reps}) ===")

    for titulo, texto, esperado in CASOS:
        latencias, turnos = [], []
        repo = resp = None
        for _ in range(args.reps):
            dt, n, repo, resp = await _una_corrida(llm, texto)
            latencias.append(dt)
            turnos.append(n)
        print(f"\n{'─'*78}\n▶ {titulo}")
        print(f"  Mensaje: «{texto[:110]}{'…' if len(texto) > 110 else ''}»")
        print(f"  Esperado: {esperado}")
        print(f"  Latencia mediana: {statistics.median(latencias):.0f}ms  "
              f"(muestras {[round(x) for x in latencias]}) · turnos de Claude: {turnos}")
        print(f"  Registró (última corrida):")
        print(_fmt_tx(repo))
        print(f"  Respuesta: {resp[:260]!r}")

    print(f"\n{'─'*78}\nRecordatorio: son latencias del AGENTE (FakeRepo). En producción suma "
          f"~0.5s de guardrail y ~0.6-1s por escritura real en Supabase.\n")


if __name__ == "__main__":
    asyncio.run(main())
