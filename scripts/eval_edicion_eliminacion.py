"""Evaluación del flujo de ACTUALIZAR y ELIMINAR consumos e ingresos (agente real).

Verifica, en conversaciones de varios turnos contra Haiku (según .env) con
FakeRepo (no toca Supabase), que ante un pedido de corregir o borrar un
movimiento, el agente:

  1. PIDE CONFIRMACIÓN antes de aplicar (el movimiento NO cambia en el turno del
     pedido; recién cambia cuando el usuario dice "sí"),
  2. NUNCA crea un ticket por esto (no es un caso sensible),
  3. aplica el cambio/borrado solo tras el "sí".

editar_transaccion y eliminar_transaccion devuelven 'requiere_confirmacion' en
la primera llamada SIN tocar nada; el agente debe preguntar y solo repetir la
tool con confirmado=true tras el "sí" del usuario.

Uso:  python -m scripts.eval_edicion_eliminacion
Requiere ANTHROPIC_API_KEY real. Cuesta unos centavos.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from app.adapters.llm.claude import ClaudeProvider
from app.application.agents.principal import MainAgent
from app.domain.models import (
    AgentContext, IncomingMessage, Message, Rol, Transaction, TransactionStatus, TransactionTipo,
)
from app.infra.config import Settings
from tests.test_agente import FakeSoporte
from tests.test_walking_skeleton import FakeRepo


class GrabadorLLM(ClaudeProvider):
    """Registra los nombres de tools emitidos en el turno conversacional actual."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.tools_turno: list[str] = []

    async def complete(self, **k):
        r = await super().complete(**k)
        self.tools_turno += [tc.nombre for tc in r.tool_calls]
        return r


def _seed_gasto(repo: FakeRepo, user_id):
    return repo.save_transaction(Transaction(
        user_id=user_id, tipo=TransactionTipo.GASTO, monto=Decimal("45"),
        fecha=date.today(), categoria="comida", comercio="Supermaxi",
        status=TransactionStatus.CONFIRMADA))


def _seed_ingreso(repo: FakeRepo, user_id):
    return repo.save_transaction(Transaction(
        user_id=user_id, tipo=TransactionTipo.INGRESO, monto=Decimal("450"),
        fecha=date.today(), categoria="Salario", comercio="Acme",
        status=TransactionStatus.CONFIRMADA))


def _snap(repo, tid):
    # TransactionStatus hereda de str, así que hay que sacar .value explícitamente
    # (isinstance(x, str) es True para el enum y str(enum) da el repr, no el valor).
    for t in repo.transactions:
        if t.id == tid:
            status = getattr(t.status, "value", t.status)
            return f"${t.monto} · {t.categoria} · {status}"
    return "(no existe)"


async def _turno(agente, llm, hist, user_id, telefono, texto):
    llm.tools_turno = []
    ctx = AgentContext(
        user=agente._repo.get_user(user_id),
        incoming=IncomingMessage(canal="web", telefono=telefono, texto=texto),
        historial=list(hist))
    r = await agente.handle(ctx)
    hist.append(Message(user_id=user_id, rol=Rol.USUARIO, contenido=texto))
    hist.append(Message(user_id=user_id, rol=Rol.ASISTENTE, contenido=r.respuesta))
    return list(llm.tools_turno), r.respuesta


async def escenario(nombre, llm, seed_fn, tid_label, peticion, confirmacion, esperado_final):
    repo = FakeRepo()
    user = repo.get_or_create_user("+50370000000", "Ana")
    tx = seed_fn(repo, user.id)
    agente = MainAgent(llm=llm, repo=repo, soporte=FakeSoporte(
        {"encontrado_en_corpus": False, "respuesta": "", "cita": None}))
    hist: list[Message] = []
    sembrado_str = _snap(repo, tx.id)

    print(f"\n{'═'*80}\n▶ {nombre}")
    print(f"  Sembrado: {tid_label} = {sembrado_str}")

    # Turno 1: el usuario PIDE el cambio/borrado.
    tools1, resp1 = await _turno(agente, llm, hist, user.id, user.telefono, peticion)
    intacto = _snap(repo, tx.id)  # ¿cambió en el turno del pedido?
    print(f"\n  Turno 1 (usuario): «{peticion}»")
    print(f"    tools: {tools1}   tickets: {len(repo.tickets)}")
    print(f"    estado tras pedir: {intacto}")
    print(f"    Luca: {resp1[:200]!r}")

    # Turno 2: el usuario CONFIRMA.
    tools2, resp2 = await _turno(agente, llm, hist, user.id, user.telefono, confirmacion)
    final = _snap(repo, tx.id)
    print(f"\n  Turno 2 (usuario): «{confirmacion}»")
    print(f"    tools: {tools2}   tickets: {len(repo.tickets)}")
    print(f"    estado final: {final}")
    print(f"    Luca: {resp2[:200]!r}")

    # Veredicto.
    sin_ticket = len(repo.tickets) == 0
    aplico = esperado_final in final
    # "pidió confirmación" = el movimiento NO cambió en el turno del pedido: sigue
    # exactamente como se sembró. Recién debe cambiar tras el "sí".
    no_cambio_en_turno1 = (intacto == sembrado_str)
    ok = no_cambio_en_turno1 and aplico and sin_ticket
    print(f"\n  VEREDICTO: {'✅ OK' if ok else '❌ REVISAR'} — "
          f"pidió confirmación (intacto en turno 1)={no_cambio_en_turno1} · "
          f"aplicó tras 'sí'={aplico} · sin ticket={sin_ticket}")


async def main():
    s = Settings.from_env()
    llm = GrabadorLLM(s.anthropic_api_key, s.claude_model, s.claude_max_tokens)
    print(f"=== Flujo actualizar/eliminar (modelo: {s.claude_model}) ===")

    await escenario(
        "ACTUALIZAR un CONSUMO (gasto)", llm, _seed_gasto, "gasto Supermaxi",
        peticion="oye ese gasto de 45 del Supermaxi cámbiamelo a 40 que me equivoqué en el monto",
        confirmacion="sí dale, confirmo",
        esperado_final="$40")

    await escenario(
        "ELIMINAR un CONSUMO (gasto)", llm, _seed_gasto, "gasto Supermaxi",
        peticion="borra el gasto del Supermaxi, ese no va, lo puse por error",
        confirmacion="sí, bórralo",
        esperado_final="anulada")

    await escenario(
        "ACTUALIZAR un INGRESO", llm, _seed_ingreso, "ingreso Acme",
        peticion="el ingreso de Acme no fueron 450, fueron 480, corrígemelo por fa",
        confirmacion="correcto, confírmalo",
        esperado_final="$480")

    await escenario(
        "ELIMINAR un INGRESO", llm, _seed_ingreso, "ingreso Acme",
        peticion="elimina ese ingreso de Acme, lo registré por error no me pagaron eso",
        confirmacion="sí, elimínalo",
        esperado_final="anulada")

    print(f"\n{'═'*80}\nClave: 'intacto en turno 1'=True significa que Luca PIDIÓ confirmación "
          f"antes de tocar nada. 'sin ticket'=True significa que no escaló.\n")


if __name__ == "__main__":
    asyncio.run(main())
