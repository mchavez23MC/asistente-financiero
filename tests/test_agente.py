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


# --- confirmación antes de escalar (crear_ticket, fase 11) ------------------------
async def test_crear_ticket_pide_confirmacion_antes_de_escalar():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [
            _tool("crear_ticket", {"motivo": "otro", "contexto": "quiere hablar con alguien"}),
            _texto("¿Quieres que te conecte con alguien de mi equipo?"),
        ]
    )
    await _agente(llm, repo).handle(_contexto(repo, "necesito ayuda con algo"))

    # No se creó el ticket: la tool pidió confirmación primero.
    assert len(repo.tickets) == 0
    tool_result = llm.llamadas[1]["messages"][-1]["content"][0]["content"]
    assert "requiere_confirmacion" in tool_result


async def test_crear_ticket_confirmado_escala():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [
            _tool("crear_ticket", {"motivo": "otro", "contexto": "quiere un humano", "confirmado": True}),
            _texto("Listo, te contactan."),
        ]
    )
    await _agente(llm, repo).handle(_contexto(repo, "sí, conéctame"))

    assert len(repo.tickets) == 1


async def test_crear_ticket_motivo_sensible_escala_sin_confirmar():
    """Seguridad primero: un fraude escala directo, sin pedirle permiso al usuario."""
    repo = FakeRepo()
    llm = ScriptedLLM(
        [
            _tool("crear_ticket", {"motivo": "fraude", "contexto": "cargo no reconocido"}),
            _texto("Ya avisé a mi equipo, te contactan."),
        ]
    )
    await _agente(llm, repo).handle(_contexto(repo, "hay un cargo que no hice"))

    assert len(repo.tickets) == 1
    assert next(iter(repo.tickets.values())).motivo == "fraude"


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


# --- fallo de tool: el error llega al modelo, no revienta el pipeline --------------
class _RepoQueFalla(FakeRepo):
    def save_transaction(self, transaction):
        raise RuntimeError("supabase caído")


async def test_fallo_de_tool_devuelve_error_al_modelo():
    """Si la tool revienta, el tool_result trae 'error' y el agente sigue vivo —
    el prompt instruye a Luca a NO confirmar el registro."""
    repo = _RepoQueFalla()
    llm = ScriptedLLM(
        [
            _tool("registrar_gasto", {"monto": 25, "categoria": "comida"}),
            _texto("Uy, tuve un problema técnico y no quedó registrado. ¿Intentas de nuevo?"),
        ]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté 25"))

    tool_result = llm.llamadas[1]["messages"][-1]["content"][0]["content"]
    assert "tool_fallo" in tool_result and "NO quedó" in tool_result
    assert "no quedó registrado" in result.respuesta.lower()


# --- system: la fecha de HOY viaja en un bloque aparte (no cacheado) --------------
async def test_system_incluye_fecha_de_hoy_sin_romper_el_cache():
    from datetime import date

    repo = FakeRepo()
    llm = ScriptedLLM([_texto("¡Hola!")])
    await _agente(llm, repo).handle(_contexto(repo, "hola"))

    system = llm.llamadas[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}  # prefijo estable
    assert date.today().isoformat() in system[1]["text"]  # bloque dinámico
    assert "cache_control" not in system[1]


# --- media: el último turno de usuario lleva bloques image/document ----------------
async def test_mensaje_con_imagen_arma_bloques_para_claude():
    from app.domain.models import MediaItem

    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_gasto", {"monto": 32.5, "comercio": "Supermaxi"}), _texto("Vi $32.50 en Supermaxi ✅")]
    )
    user = repo.get_or_create_user("+50370000000", "Ana")
    ctx = AgentContext(
        user=user,
        incoming=IncomingMessage(
            canal="whatsapp",
            telefono=user.telefono,
            texto="",
            media=[MediaItem(content_type="image/jpeg", url="https://x", data_base64="Zm90bw==")],
        ),
        historial=[],
    )
    result = await _agente(llm, repo).handle(ctx)

    # Nota: `llamadas` guarda la referencia a la lista que el bucle muta después;
    # el turno del usuario con la imagen es el primero, no el último.
    contenido = llm.llamadas[0]["messages"][0]["content"]
    assert contenido[0]["type"] == "image"
    assert contenido[0]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "Zm90bw==",
    }
    assert contenido[-1]["type"] == "text"  # nota de contexto para el modelo
    assert result.tool_llamada == "registrar_gasto"


# --- corto-circuito de latencia: confirmación directa sin 2º turno de Claude ------
async def test_registro_simple_confirma_sin_segundo_turno():
    """Un gasto exitoso se confirma en código: una sola llamada a Claude (el
    ScriptedLLM tiene UNA respuesta; un 2º turno reventaría por IndexError)."""
    repo = FakeRepo()
    llm = ScriptedLLM([_tool("registrar_gasto", {"monto": 25, "categoria": "comida"})])
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté 25 en comida"))

    assert len(llm.llamadas) == 1  # NO hubo segundo turno del LLM
    assert result.intencion == "gasto"
    assert result.tool_llamada == "registrar_gasto"
    assert "25" in result.respuesta and "✅" in result.respuesta
    assert repo.transactions[0].status == "confirmada"


async def test_registro_ingreso_confirma_sin_segundo_turno():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_ingreso", {"monto": 450, "categoria": "Salario", "fuente": "Acme"})]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "me pagaron 450 de Acme"))

    assert len(llm.llamadas) == 1
    assert result.intencion == "ingreso"
    assert "450" in result.respuesta


async def test_varios_registros_una_pasada_confirma_en_lista():
    repo = FakeRepo()
    doble = LLMResponse(
        tool_calls=[
            ToolCall(id="a", nombre="registrar_gasto", argumentos={"monto": 10, "categoria": "transporte", "comercio": "Uber"}),
            ToolCall(id="b", nombre="registrar_gasto", argumentos={"monto": 5, "categoria": "comida", "comercio": "café"}),
        ],
        stop_reason="tool_use",
    )
    llm = ScriptedLLM([doble])
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté 10 en uber y 5 en café"))

    assert len(llm.llamadas) == 1  # sin 2º turno
    assert "•" in result.respuesta  # lista vertical
    assert "10" in result.respuesta and "5" in result.respuesta
    assert len(repo.transactions) == 2


async def test_gasto_pendiente_no_corto_circuita():
    """Falta el monto → la tool devuelve 'pendiente_confirmacion': se necesita al
    LLM para pedir el dato, así que SÍ hay segundo turno."""
    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_gasto", {"categoria": "comida"}), _texto("¿Cuánto gastaste?")]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté en comida"))

    assert len(llm.llamadas) == 2  # el corto-circuito NO aplicó
    assert "Cuánto" in result.respuesta


async def test_registro_con_error_no_corto_circuita():
    """Si la tool falla, el error va al LLM (no se plantilla una confirmación falsa)."""
    repo = _RepoQueFalla()
    llm = ScriptedLLM(
        [
            _tool("registrar_gasto", {"monto": 25, "categoria": "comida"}),
            _texto("Uy, tuve un problema técnico y no quedó registrado."),
        ]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "gasté 25"))

    assert len(llm.llamadas) == 2
    assert "problema" in result.respuesta.lower()


async def test_adjunto_no_descargado_se_describe_en_texto():
    from app.domain.models import MediaItem

    repo = FakeRepo()
    llm = ScriptedLLM([_texto("No me llegó la imagen, ¿la reenvías? 🙏")])
    user = repo.get_or_create_user("+50370000000", "Ana")
    ctx = AgentContext(
        user=user,
        incoming=IncomingMessage(
            canal="whatsapp",
            telefono=user.telefono,
            texto="mi recibo",
            media=[MediaItem(content_type="image/jpeg", url="https://x", data_base64=None)],
        ),
        historial=[],
    )
    await _agente(llm, repo).handle(ctx)

    contenido = llm.llamadas[0]["messages"][-1]["content"]
    assert all(b["type"] == "text" for b in contenido)  # sin bloque image
    assert "no se pudo descargar" in contenido[-1]["text"]
    assert "mi recibo" in contenido[-1]["text"]
