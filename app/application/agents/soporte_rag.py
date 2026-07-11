"""H3 — soporte por contexto directo con caché (§4) — Fase 5.

El corpus completo (~decenas de docs markdown en app/kb/) va como bloque de
sistema estable y primero, marcado con `cache_control` para maximizar hits de
prompt caching (§4). Guardrail de grounding: si la respuesta no está en el
corpus, NO se inventa — se señala para escalar a ticket.

Se usa como tool del agente principal (opción C, §1): `responder_soporte` llama
aquí; el resultado dice si se encontró en el corpus o hay que crear ticket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.domain.ports import LLMProvider

KB_DIR = Path(__file__).resolve().parents[2] / "kb"

SISTEMA_GROUNDING = """\
Eres el agente de soporte de un asistente financiero. Responde ÚNICAMENTE con \
base en el corpus de conocimiento que se te da a continuación. Reglas:
- Si la respuesta está en el corpus: respóndela clara y breve, en español, y \
cita textualmente la frase del corpus en la que te basas.
- Si la respuesta NO está en el corpus: no inventes ni supongas. Indica que no \
puedes resolverlo y que se escalará a un humano.

Devuelve SOLO JSON válido:
{"encontrado_en_corpus": true|false, "respuesta": "texto para el usuario", "cita": "frase textual del corpus o null"}"""


def cargar_corpus(kb_dir: Path = KB_DIR) -> str:
    """Concatena los .md del corpus en un solo bloque estable (orden alfabético)."""
    if not kb_dir.exists():
        return ""
    partes = []
    for md in sorted(kb_dir.glob("*.md")):
        partes.append(f"# DOCUMENTO: {md.stem}\n\n{md.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(partes)


class SoporteRAG:
    def __init__(self, llm: LLMProvider, corpus: Optional[str] = None) -> None:
        self._llm = llm
        self._corpus = corpus if corpus is not None else cargar_corpus()

    def _system_blocks(self) -> list[dict]:
        # El corpus va primero y estable → cache_control para el hit de caché (§4).
        return [
            {"type": "text", "text": SISTEMA_GROUNDING},
            {
                "type": "text",
                "text": f"CORPUS DE CONOCIMIENTO:\n\n{self._corpus}",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    async def responder(self, pregunta: str) -> dict:
        """Devuelve {encontrado_en_corpus, respuesta, cita}. Sin corpus → escala."""
        if not self._corpus:
            return {
                "encontrado_en_corpus": False,
                "respuesta": "No tengo base de conocimiento cargada para responder eso.",
                "cita": None,
            }
        resp = await self._llm.complete(
            messages=[{"role": "user", "content": pregunta}],
            system=self._system_blocks(),
        )
        try:
            data = json.loads(resp.texto or "{}")
            return {
                "encontrado_en_corpus": bool(data.get("encontrado_en_corpus", False)),
                "respuesta": str(data.get("respuesta", "")),
                "cita": data.get("cita"),
            }
        except (json.JSONDecodeError, TypeError):
            # Grounding fail-safe: si la salida no es parseable, no arriesgar → escalar.
            return {
                "encontrado_en_corpus": False,
                "respuesta": "No pude resolver tu consulta con certeza.",
                "cita": None,
            }
