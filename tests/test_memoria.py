"""Parte A del plan — memoria semántica: retrieval híbrido, indexación,
inyección al agente, hechos de largo plazo y resúmenes. Todo con fakes en
memoria (embedder scripteado + FakeRepo con coseno real), sin red.

Diseño clave verificado aquí: la memoria degrada con gracia. Si el embedder
falla o no está, el pipeline responde igual (memoria vacía).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.application.memoria import MemoriaSemantica, render_memoria
from app.application.memoria_jobs import (
    extraer_hechos_usuario,
    resumir_conversaciones_inactivas,
)
from app.application.process_message import ProcessMessage
from app.application.router import InMemoryAgentRegistry
from app.domain.models import (
    AgentContext,
    AgentResult,
    IncomingMessage,
    Intencion,
    Message,
    Recuerdo,
    Rol,
    UserFact,
)

from tests.test_agente import ScriptedLLM, _agente, _tool, _texto
from tests.test_walking_skeleton import FakeChannel, FakeRepo, StubGuardrail


# --- embedder fake -----------------------------------------------------------
class FakeEmbedder:
    """Embeddings deterministas por palabra clave: cada texto se vuelve un vector
    disperso sobre un vocabulario fijo. Textos que comparten palabras quedan
    cercanos en coseno, así el retrieval es verificable sin un modelo real."""

    dimensiones = 8
    _VOCAB = ["gato", "carro", "sueldo", "comida", "viaje", "perro", "renta", "cafe"]

    def __init__(self) -> None:
        self.llamadas: list[tuple[list[str], str]] = []

    async def embed(self, textos, tipo="document"):
        self.llamadas.append((textos, tipo))
        vecs = []
        for t in textos:
            tl = t.lower()
            v = [1.0 if palabra in tl else 0.0 for palabra in self._VOCAB]
            if not any(v):  # texto sin vocabulario conocido → vector neutro
                v = [0.1] * len(self._VOCAB)
            vecs.append(v)
        return vecs


class ExplotaEmbedder:
    dimensiones = 8

    async def embed(self, textos, tipo="document"):
        raise RuntimeError("Voyage caído")


def _user(repo: FakeRepo):
    return repo.get_or_create_user("+50370000000", "Ana")


# --- A2: retrieval híbrido ---------------------------------------------------
async def test_recuerda_mensaje_viejo_por_similitud():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    # Un mensaje viejo sobre el carro, ya indexado, FUERA de la ventana reciente.
    viejo = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="mi carro es un Corolla"))
    vec = (await emb.embed(["mi carro es un Corolla"]))[0]
    repo.save_message_embedding(viejo.id, user.id, vec)

    memoria = MemoriaSemantica(repo, emb, umbral=0.5)
    recuerdos, _ = await memoria.recordar(user.id, "cuánto gasté en el carro", ventana=[])

    assert any("Corolla" in r.contenido for r in recuerdos)
    assert recuerdos[0].origen == "mensaje"


async def test_no_repite_lo_que_ya_esta_en_la_ventana():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    m = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="mi carro es un Corolla"))
    repo.save_message_embedding(m.id, user.id, (await emb.embed(["mi carro es un Corolla"]))[0])

    memoria = MemoriaSemantica(repo, emb, umbral=0.5)
    # El mismo mensaje está en la ventana reciente → no debe volver como "recuerdo".
    recuerdos, _ = await memoria.recordar(user.id, "algo del carro", ventana=[m])

    assert all("Corolla" not in r.contenido for r in recuerdos)


async def test_recuerda_incluye_resumenes():
    from app.domain.models import ConversationSummary

    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    resumen = ConversationSummary(user_id=user.id, resumen="el usuario planea un viaje")
    repo.save_conversation_summary(resumen, embedding=(await emb.embed(["el usuario planea un viaje"]))[0])

    memoria = MemoriaSemantica(repo, emb, umbral=0.5)
    recuerdos, _ = await memoria.recordar(user.id, "cuánto para el viaje", ventana=[])

    assert any(r.origen == "resumen" and "viaje" in r.contenido for r in recuerdos)


async def test_embedder_caido_no_rompe_devuelve_vacio():
    repo = FakeRepo()
    user = _user(repo)
    memoria = MemoriaSemantica(repo, ExplotaEmbedder())
    recuerdos, hechos = await memoria.recordar(user.id, "hola", ventana=[])
    assert recuerdos == [] and hechos == []


async def test_indexar_guarda_el_vector():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    m = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="pago la renta"))
    await MemoriaSemantica(repo, emb).indexar(m.id, user.id, "pago la renta")
    assert m.id in repo.message_embeddings


# --- A2: inyección al agente (render + system block) --------------------------
def test_render_memoria_arma_bloque_con_hechos_y_recuerdos():
    hechos = [UserFact(user_id=uuid4(), contenido="le pagan el 30")]
    recuerdos = [Recuerdo(contenido="compró un carro", origen="mensaje", rol="user", similitud=0.8)]
    texto = render_memoria(recuerdos, hechos)
    assert "le pagan el 30" in texto
    assert "compró un carro" in texto


def test_render_memoria_vacia_es_none():
    assert render_memoria([], []) is None


async def test_agente_recibe_la_memoria_en_el_system():
    """El bloque de memoria viaja como bloque de sistema dinámico, no cacheado."""
    repo = FakeRepo()
    user = _user(repo)
    llm = ScriptedLLM([_texto("¡Claro!")])
    ctx = AgentContext(
        user=user,
        incoming=IncomingMessage(canal="web", telefono=user.telefono, texto="cómo voy"),
        historial=[],
        memoria_relevante=[Recuerdo(contenido="compró un Corolla", origen="mensaje", rol="user")],
        hechos_usuario=[UserFact(user_id=user.id, contenido="le pagan el 30")],
    )
    await _agente(llm, repo).handle(ctx)

    system = llm.llamadas[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}  # prefijo estable intacto
    textos = " ".join(b["text"] for b in system)
    assert "Corolla" in textos and "le pagan el 30" in textos
    # El bloque de memoria NO lleva cache_control (es dinámico).
    assert "cache_control" not in system[-1]


async def test_agente_sin_memoria_no_agrega_bloque():
    """Sin memoria recuperada, ningún bloque de memoria entra al system."""
    repo = FakeRepo()
    llm = ScriptedLLM([_texto("hola")])
    user = _user(repo)
    ctx = AgentContext(
        user=user,
        incoming=IncomingMessage(canal="web", telefono=user.telefono, texto="hola"),
        historial=[],
    )
    await _agente(llm, repo).handle(ctx)
    textos = " ".join(b["text"] for b in llm.llamadas[0]["system"])
    assert "RECUERDOS RELEVANTES" not in textos
    assert "LO QUE SABES DEL USUARIO" not in textos


# --- integración con ProcessMessage (run_agent recupera e indexa) ------------
class HandlerCaptura:
    intent = "principal"

    def __init__(self) -> None:
        self.contexto = None

    async def handle(self, context):
        self.contexto = context
        return AgentResult(respuesta="listo ✅", intencion=Intencion.OTRO)


def _pipeline_con_memoria(emb):
    repo, channel = FakeRepo(), FakeChannel()
    handler = HandlerCaptura()
    registry = InMemoryAgentRegistry()
    registry.register(handler)
    memoria = MemoriaSemantica(repo, emb, umbral=0.5)
    # historial_n=1: la ventana reciente solo tiene el mensaje actual, así el
    # mensaje viejo queda FUERA de la ventana y debe llegar por recuperación.
    process = ProcessMessage(
        repo, StubGuardrail(), registry, channel, historial_n=1, memoria=memoria
    )
    return process, repo, channel, handler


async def test_run_agent_inyecta_memoria_e_indexa():
    emb = FakeEmbedder()
    process, repo, channel, handler = _pipeline_con_memoria(emb)
    user = _user(repo)
    user = repo.registrar_consentimiento(user.id)
    # Mensaje viejo indexado sobre el carro (fuera de la ventana que se armará).
    viejo = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="tengo un carro rojo"))
    repo.save_message_embedding(viejo.id, user.id, (await emb.embed(["tengo un carro rojo"]))[0])

    await process(IncomingMessage(canal="fake", telefono=user.telefono, texto="cuánto gasté en el carro"))

    # El handler vio la memoria recuperada.
    assert any("carro rojo" in r.contenido for r in handler.contexto.memoria_relevante)
    # Y los mensajes de este turno quedaron indexados (usuario + respuesta).
    contenidos_indexados = {
        m.contenido for m in repo.messages if m.id in repo.message_embeddings
    }
    assert "cuánto gasté en el carro" in contenidos_indexados
    assert "listo ✅" in contenidos_indexados


# --- A3: extracción de hechos ------------------------------------------------
async def test_extrae_y_guarda_hechos():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="mi sueldo es 450 y me pagan el 30"))
    llm = ScriptedLLM([_texto('{"hechos": [{"tipo": "dato", "contenido": "le pagan el 30"}]}')])

    n = await extraer_hechos_usuario(repo, llm, emb, ventana_horas=48)

    assert n == 1
    assert any("le pagan el 30" in f.contenido for f in repo.get_user_facts(user.id))


async def test_hecho_duplicado_se_actualiza_no_se_duplica():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="hablo de mi sueldo"))
    # Ya existe un hecho idéntico (mismo texto → mismo vector → similitud 1.0).
    repo.upsert_user_fact(
        UserFact(user_id=user.id, tipo="dato", contenido="su sueldo es 450"),
        embedding=(await emb.embed(["su sueldo es 450"]))[0],
    )
    llm = ScriptedLLM([_texto('{"hechos": [{"tipo": "dato", "contenido": "su sueldo es 450"}]}')])

    await extraer_hechos_usuario(repo, llm, emb, ventana_horas=48)

    # No se duplicó: sigue habiendo un solo hecho.
    assert len(repo.get_user_facts(user.id)) == 1


# --- A4: resúmenes de conversación -------------------------------------------
async def test_resume_conversacion_inactiva():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    viejo_ts = datetime.now(timezone.utc) - timedelta(hours=10)
    repo.messages.append(
        Message(id=uuid4(), user_id=user.id, rol=Rol.USUARIO, contenido="anoté varios gastos de comida", timestamp=viejo_ts)
    )
    llm = ScriptedLLM([_texto('{"resumen": "el usuario registró gastos de comida"}')])

    n = await resumir_conversaciones_inactivas(repo, llm, emb, inactividad_horas=6)

    assert n == 1
    assert repo.conversation_summaries
    resumen_guardado = repo.conversation_summaries[0][0]
    assert "comida" in resumen_guardado.resumen


async def test_no_resume_sesion_todavia_activa():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = _user(repo)
    # Mensaje reciente (hace 1h): la sesión sigue viva → no se resume aún.
    repo.messages.append(
        Message(id=uuid4(), user_id=user.id, rol=Rol.USUARIO, contenido="hola",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=1))
    )
    llm = ScriptedLLM([_texto('{"resumen": "no debería usarse"}')])

    n = await resumir_conversaciones_inactivas(repo, llm, emb, inactividad_horas=6)
    assert n == 0
