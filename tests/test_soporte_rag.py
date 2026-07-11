"""Fase 5 — soporte H3 (grounding) con LLM scripteado.

Gate: pregunta cubierta → respuesta citando el corpus; fuera del corpus → se
señala para ticket, cero alucinación. También se verifica que el corpus va con
`cache_control` (§4).
"""

from __future__ import annotations

import json

from app.application.agents.soporte_rag import SoporteRAG, cargar_corpus


class OneShotLLM:
    def __init__(self, contenido: str) -> None:
        self._contenido = contenido
        self.system = None

    async def complete(self, messages, tools=None, system=None):
        from app.domain.models import LLMResponse

        self.system = system
        return LLMResponse(texto=self._contenido, stop_reason="end_turn")


def test_corpus_real_se_carga():
    corpus = cargar_corpus()
    assert "DOCUMENTO" in corpus and "registro" in corpus.lower()


async def test_pregunta_en_corpus_devuelve_respuesta_y_cita():
    llm = OneShotLLM(json.dumps({"encontrado_en_corpus": True, "respuesta": "Escribe el monto.", "cita": "El monto es obligatorio"}))
    rag = SoporteRAG(llm, corpus="El monto es obligatorio para confirmar un gasto.")
    r = await rag.responder("¿qué pasa si no doy el monto?")
    assert r["encontrado_en_corpus"] is True
    assert r["cita"]
    # El corpus va con cache_control (§4).
    bloque_corpus = llm.system[-1]
    assert bloque_corpus["cache_control"] == {"type": "ephemeral"}


async def test_pregunta_fuera_de_corpus_no_alucina():
    llm = OneShotLLM(json.dumps({"encontrado_en_corpus": False, "respuesta": "No lo sé, te escalo.", "cita": None}))
    rag = SoporteRAG(llm, corpus="Corpus sobre gastos.")
    r = await rag.responder("¿cuál es la capital de Francia?")
    assert r["encontrado_en_corpus"] is False


async def test_json_invalido_es_fail_safe_a_escalar():
    llm = OneShotLLM("esto no es json")
    rag = SoporteRAG(llm, corpus="algo")
    r = await rag.responder("x")
    assert r["encontrado_en_corpus"] is False


async def test_sin_corpus_escala():
    rag = SoporteRAG(OneShotLLM("{}"), corpus="")
    r = await rag.responder("cualquier cosa")
    assert r["encontrado_en_corpus"] is False
