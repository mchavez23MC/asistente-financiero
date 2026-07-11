"""Fase 3 — guardrail fail-closed (§1.4 / §7.3).

Gate de la fase:
  - frase con término de denylist → sensible sin inferencia (capa 1);
  - clasificación con baja confianza → forzada sensible (capa 2);
  - Groq caído o colgado → fail-closed, el mensaje no fluye sin clasificar;
  - ruta sensible → Ticket con contexto y el agente NUNCA se invoca.
"""

from __future__ import annotations

import asyncio

from app.adapters.guardrail.denylist import match_denylist
from app.adapters.guardrail.layered import LayeredGuardrail
from app.application.process_message import (
    RESPUESTA_FAIL_CLOSED,
    RESPUESTA_SENSIBLE,
    ProcessMessage,
)
from app.application.router import InMemoryAgentRegistry
from app.domain.models import GuardrailResult, IncomingMessage

from tests.test_walking_skeleton import FakeChannel, FakeRepo


# --- fakes del clasificador Groq ------------------------------------------------
class FakeClassifier:
    def __init__(self, resultado: GuardrailResult) -> None:
        self._resultado = resultado
        self.llamadas = 0

    async def classify(self, texto: str) -> GuardrailResult:
        self.llamadas += 1
        return self._resultado


class BrokenClassifier:
    """Groq caído: toda llamada explota."""

    async def classify(self, texto: str) -> GuardrailResult:
        raise ConnectionError("groq down")


class HangingClassifier:
    """Groq colgado: nunca responde dentro del timeout."""

    async def classify(self, texto: str) -> GuardrailResult:
        await asyncio.sleep(5)
        return GuardrailResult(sensible=False)


def _ok(confianza: float = 0.95) -> GuardrailResult:
    return GuardrailResult(
        sensible=False, categoria="ninguna", confianza=confianza, fuente="clasificador"
    )


# --- capa 1: denylist ------------------------------------------------------------
def test_denylist_atrapa_terminos_de_alto_riesgo():
    assert match_denylist("quiero invertir en cripto") == "consejo_inversion"
    assert match_denylist("me hicieron una ESTAFA") == "fraude"
    assert match_denylist("voy a poner un reclamo") == "reclamo"
    assert match_denylist("Inversión con rendimiento") == "consejo_inversion"  # tildes


def test_denylist_no_atrapa_mensajes_normales():
    assert match_denylist("gasté 25 en pupusas") is None
    assert match_denylist("cuánto llevo este mes") is None


async def test_denylist_corta_sin_llamar_al_clasificador():
    classifier = FakeClassifier(_ok())
    g = LayeredGuardrail(classifier)
    r = await g.classify("quiero invertir en cripto")
    assert r.sensible and r.fuente == "denylist" and r.categoria == "consejo_inversion"
    assert classifier.llamadas == 0  # cero inferencia (gate de la fase)


# --- clasificador + capa 2: umbral -------------------------------------------------
async def test_mensaje_normal_pasa_con_confianza_alta():
    g = LayeredGuardrail(FakeClassifier(_ok(0.95)), umbral_confianza=0.7)
    r = await g.classify("gasté 25 en pupusas")
    assert not r.sensible and r.fuente == "clasificador"


async def test_baja_confianza_fuerza_sensible():
    g = LayeredGuardrail(FakeClassifier(_ok(0.4)), umbral_confianza=0.7)
    r = await g.classify("mensaje ambiguo")
    assert r.sensible and r.fuente == "umbral" and r.confianza == 0.4


async def test_clasificador_sensible_se_respeta():
    veredicto = GuardrailResult(
        sensible=True, categoria="reclamo", confianza=0.9, fuente="clasificador"
    )
    g = LayeredGuardrail(FakeClassifier(veredicto))
    r = await g.classify("no estoy conforme con lo que me cobraron")
    assert r.sensible and r.categoria == "reclamo"


# --- fail-closed (§7.3.4) -----------------------------------------------------------
async def test_groq_caido_es_fail_closed():
    g = LayeredGuardrail(BrokenClassifier())
    r = await g.classify("mensaje cualquiera")
    assert r.sensible and r.fuente == "fail_closed" and r.confianza == 0.0


async def test_groq_colgado_es_fail_closed_por_timeout():
    g = LayeredGuardrail(HangingClassifier(), timeout_ms=50)
    r = await g.classify("mensaje cualquiera")
    assert r.sensible and r.fuente == "fail_closed"


# --- ruta sensible en el orquestador -------------------------------------------------
class ExplotaSiLoLlaman:
    """El agente principal NUNCA debe recibir un mensaje sensible (§7.3)."""

    intent = "eco"

    async def handle(self, context):
        raise AssertionError("un mensaje sensible llegó al agente principal")


def _pipeline_sensible(guardrail):
    repo, channel = FakeRepo(), FakeChannel()
    registry = InMemoryAgentRegistry()
    registry.register(ExplotaSiLoLlaman())
    return ProcessMessage(repo, guardrail, registry, channel), repo, channel


def _msg(texto: str) -> IncomingMessage:
    return IncomingMessage(canal="fake", telefono="+50370000000", texto=texto)


async def test_ruta_sensible_crea_ticket_y_no_llega_al_agente():
    g = LayeredGuardrail(FakeClassifier(_ok()))
    process, repo, channel = _pipeline_sensible(g)
    await process(_msg("hola"))  # consentimiento
    await process(_msg("quiero invertir en cripto"))

    assert len(repo.tickets) == 1
    ticket = repo.tickets[0]
    assert ticket.motivo == "consejo_inversion"
    assert "fuente=denylist" in ticket.contexto
    assert ticket.mensaje_origen_id is not None
    assert channel.enviados[-1][1] == RESPUESTA_SENSIBLE
    # Audit: la respuesta de escalación quedó con intención 'sensible'.
    assert repo.messages[-1].intencion == "sensible"


async def test_fail_closed_crea_ticket_con_motivo_propio():
    g = LayeredGuardrail(BrokenClassifier())
    process, repo, channel = _pipeline_sensible(g)
    await process(_msg("hola"))
    await process(_msg("gasté 25 en pupusas"))  # normal, pero Groq está caído

    assert repo.tickets[0].motivo == "guardrail_fail_closed"
    assert channel.enviados[-1][1] == RESPUESTA_FAIL_CLOSED


async def test_fraude_escala_con_prioridad_alta():
    g = LayeredGuardrail(FakeClassifier(_ok()))
    process, repo, _ = _pipeline_sensible(g)
    await process(_msg("hola"))
    await process(_msg("me estafaron con un cobro"))
    assert repo.tickets[0].motivo == "fraude"
    assert repo.tickets[0].prioridad == "alta"
