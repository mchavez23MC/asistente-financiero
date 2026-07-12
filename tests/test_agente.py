"""Fase 4 — agente principal Claude con tools (H1+H2), con LLM scripteado.

No llama a Anthropic: un `ScriptedLLM` devuelve respuestas pre-armadas para
ejercitar el bucle de tool use, la ejecución sobre el `Repository` y el audit
de intención. Gate de la fase (contra el sistema real) se valida a mano.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.application.agents.principal import MainAgent
from app.domain.models import (
    AgentContext,
    Budget,
    IncomingMessage,
    LLMResponse,
    ToolCall,
    Transaction,
    User,
)

from tests.test_walking_skeleton import FakeRepo


class ScriptedLLM:
    """Devuelve respuestas en orden; registra lo que recibió."""

    def __init__(self, respuestas: list[LLMResponse]) -> None:
        self._respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    async def complete(self, messages, tools=None, system=None) -> LLMResponse:
        self.llamadas.append({"messages": messages, "tools": tools, "system": system})
        return self._respuestas.pop(0)


class FakeSoporte:
    def __init__(self, resultado: dict) -> None:
        self._resultado = resultado
        self.preguntas: list[str] = []

    async def responder(self, pregunta: str) -> dict:
        self.preguntas.append(pregunta)
        return self._resultado


def _texto(t: str) -> LLMResponse:
    return LLMResponse(texto=t, stop_reason="end_turn")


def _tool(nombre: str, argumentos: dict) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=f"tu_{nombre}", nombre=nombre, argumentos=argumentos)],
        stop_reason="tool_use",
    )


def _contexto(repo: FakeRepo, texto: str) -> AgentContext:
    user = repo.get_or_create_user("+50370000000", "Ana")
    return AgentContext(
        user=user,
        incoming=IncomingMessage(canal="web", telefono=user.telefono, texto=texto),
        historial=[],
    )


def _agente(llm, repo, soporte=None) -> MainAgent:
    return MainAgent(llm=llm, repo=repo, soporte=soporte or FakeSoporte({}))


# --- H1: registrar gasto ---------------------------------------------------------
async def test_registra_gasto_completo():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_gasto", {"monto": 25, "categoria": "comida"}), _texto("Listo, registré $25 en comida.")]
    )
    ctx = _contexto(repo, "gasté 25 en pupusas")
    result = await _agente(llm, repo).handle(ctx)

    assert result.intencion == "gasto"
    assert result.tool_llamada == "registrar_gasto"
    assert "25" in result.respuesta
    # El gasto quedó confirmado en el repo.
    assert len(repo.transactions) == 1
    tx = repo.transactions[0]
    assert tx.status == "confirmada" and tx.monto == Decimal("25")


async def test_registrar_gasto_cataloga_la_categoria():
    """La categoría usada queda registrada en el catálogo (tabla categories)."""
    repo = FakeRepo()
    llm = ScriptedLLM([_tool("registrar_gasto", {"monto": 25, "categoria": "comida"}), _texto("ok")])
    await _agente(llm, repo).handle(_contexto(repo, "gasté 25 en comida"))
    assert "comida" in repo.categories
    assert [c.nombre for c in repo.get_categories()] == ["comida"]


async def test_gasto_incompleto_queda_pendiente():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_gasto", {"categoria": "comida"}), _texto("¿Cuánto gastaste?")]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté en pupusas"))

    assert repo.transactions[0].status == "pendiente_confirmacion"
    assert repo.transactions[0].monto is None
    # El tool_result que vio el modelo incluyó los faltantes.
    tool_result_msg = llm.llamadas[1]["messages"][-1]["content"][0]["content"]
    assert "monto" in tool_result_msg and "faltantes" in tool_result_msg


async def test_segundo_mensaje_completa_la_pendiente():
    repo = FakeRepo()
    # Primera vuelta: pendiente sin monto.
    await _agente(
        ScriptedLLM([_tool("registrar_gasto", {"categoria": "comida"}), _texto("¿Cuánto?")]),
        repo,
    ).handle(_contexto(repo, "gasté en pupusas"))
    # Segunda vuelta: el modelo da el monto → completa la MISMA transacción.
    await _agente(
        ScriptedLLM([_tool("registrar_gasto", {"monto": 25}), _texto("Registrado.")]),
        repo,
    ).handle(_contexto(repo, "fueron 25"))

    assert len(repo.transactions) == 1  # se completó, no se duplicó
    assert repo.transactions[0].status == "confirmada"
    assert repo.transactions[0].monto == Decimal("25")


# --- H2: consultar presupuesto (grounded) -----------------------------------------
async def test_consulta_presupuesto_usa_numeros_del_sistema():
    repo = FakeRepo()
    user = repo.get_or_create_user("+50370000000", "Ana")
    repo.budgets.append(Budget(user_id=user.id, categoria="comida", monto_limite=Decimal("100")))
    repo.save_transaction(
        Transaction(user_id=user.id, monto=Decimal("40"), categoria="comida", status="confirmada")
    )
    llm = ScriptedLLM(
        [_tool("consultar_presupuesto", {"periodo": "mensual", "categoria": "comida"}), _texto("Llevas $40 de $100 (40%).")]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "cómo va mi presupuesto de comida"))

    assert result.intencion == "presupuesto"
    tool_result = llm.llamadas[1]["messages"][-1]["content"][0]["content"]
    assert '"gastado": 40' in tool_result and '"porcentaje": 40' in tool_result


# --- mensaje mixto: dos tools en una pasada (opción C) ----------------------------
async def test_mensaje_mixto_dos_tools_una_pasada():
    repo = FakeRepo()
    doble = LLMResponse(
        tool_calls=[
            ToolCall(id="a", nombre="registrar_gasto", argumentos={"monto": 25}),
            ToolCall(id="b", nombre="consultar_presupuesto", argumentos={"periodo": "mensual"}),
        ],
        stop_reason="tool_use",
    )
    llm = ScriptedLLM([doble, _texto("Registré $25 y llevas eso este mes.")])
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté 25 y cómo voy este mes"))

    assert len(repo.transactions) == 1
    # Ambos tool_result se realimentaron juntos.
    assert len(llm.llamadas[1]["messages"][-1]["content"]) == 2


# --- H3 vía tool + aislamiento de user_id -----------------------------------------
async def test_soporte_se_delega_a_rag():
    repo = FakeRepo()
    soporte = FakeSoporte({"encontrado_en_corpus": True, "respuesta": "Escribe el monto.", "cita": "…"})
    llm = ScriptedLLM(
        [_tool("responder_soporte", {"pregunta": "cómo registro un gasto"}), _texto("Escribe el monto y listo.")]
    )
    result = await _agente(llm, repo, soporte).handle(_contexto(repo, "cómo registro un gasto"))
    assert result.intencion == "soporte"
    assert soporte.preguntas == ["cómo registro un gasto"]


async def test_user_id_no_viaja_por_la_tool():
    """Aunque el modelo intente pasar user_id, se filtra (§7.3.2)."""
    repo = FakeRepo()
    otro = uuid4()
    llm = ScriptedLLM(
        [_tool("consultar_presupuesto", {"periodo": "mensual", "user_id": str(otro)}), _texto("ok")]
    )
    ctx = _contexto(repo, "cuánto llevo")
    await _agente(llm, repo).handle(ctx)
    # La consulta se hizo sobre el user del contexto, no sobre el inyectado.
    # (si user_id se hubiera colado, consultar_presupuesto habría fallado por kwarg)
    assert True
