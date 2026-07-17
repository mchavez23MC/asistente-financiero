"""Memoria semántica: recuperación híbrida e indexación (Parte A del plan).

Convierte la "memoria plana" (log cronológico, ventana fija de 10 mensajes) en
memoria semántica: además de la ventana reciente (recencia), se recuperan por
SIMILITUD los mensajes viejos relevantes al mensaje entrante, los resúmenes de
conversaciones pasadas y los hechos estables del usuario.

Regla de oro: la memoria NUNCA rompe el pipeline. Si el embedder o la búsqueda
vectorial fallan (Voyage caído, RPC inexistente porque no se corrió la
migración), se degrada a memoria vacía y el asistente sigue con la ventana
reciente, exactamente como antes de esta feature. Por eso todo va envuelto en
try/except y el composition root inyecta esto solo si hay embedder configurado.

Las llamadas al `Repository` son síncronas (contrato de ports.py) y aquí se
corren en hilos (`to_thread`) para no congelar el event loop, igual que en el
orquestador.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from uuid import UUID

from app.domain.models import Message, Recuerdo, UserFact
from app.domain.ports import EmbeddingProvider, Repository

log = logging.getLogger("e5.memoria")


class MemoriaSemantica:
    def __init__(
        self,
        repo: Repository,
        embedder: EmbeddingProvider,
        *,
        top_k_mensajes: int = 5,
        top_k_resumenes: int = 3,
        max_hechos: int = 10,
        umbral: float = 0.75,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._top_k_mensajes = top_k_mensajes
        self._top_k_resumenes = top_k_resumenes
        self._max_hechos = max_hechos
        self._umbral = umbral

    async def recordar(
        self, user_id: UUID, consulta: str, ventana: list[Message]
    ) -> tuple[list[Recuerdo], list[UserFact]]:
        """Recuperación híbrida para un mensaje entrante. Devuelve
        (recuerdos_relevantes, hechos_del_usuario):

        - recuerdos: mensajes viejos y resúmenes semánticamente cercanos a la
          `consulta`, EXCLUYENDO los mensajes que ya están en la `ventana`
          reciente (no repetir lo que el agente ya ve).
        - hechos: memoria de largo plazo del usuario (no depende de la consulta).

        Ante cualquier fallo devuelve lo que haya podido reunir (posiblemente
        vacío): la memoria es un extra, no un requisito para responder."""
        if not consulta.strip():
            return [], await self._solo_hechos(user_id)
        try:
            vecs = await self._embedder.embed([consulta], tipo="query")
        except Exception:
            log.warning("Embedding de consulta falló; memoria semántica omitida", exc_info=True)
            return [], await self._solo_hechos(user_id)

        qvec = vecs[0]
        mensajes, resumenes, hechos = await asyncio.gather(
            self._safe(self._repo.match_messages, user_id, qvec, self._top_k_mensajes, self._umbral),
            self._safe(self._repo.match_summaries, user_id, qvec, self._top_k_resumenes),
            self._safe(self._repo.get_user_facts, user_id, self._max_hechos),
        )

        en_ventana = {m.contenido for m in ventana}
        recuerdos = [r for r in (mensajes or []) if r.contenido not in en_ventana]
        recuerdos.extend(resumenes or [])
        # Más parecido primero; los resúmenes se intercalan por su similitud.
        recuerdos.sort(key=lambda r: r.similitud, reverse=True)
        return recuerdos, (hechos or [])

    async def indexar(self, message_id: UUID, user_id: UUID, texto: str) -> None:
        """Calcula y guarda el vector de un mensaje ya persistido (para que sea
        recuperable en el futuro). Pensado para correr en background tras enviar
        la respuesta: no debe bloquear ni romper nada."""
        await self.indexar_lote([(message_id, texto)], user_id)

    async def indexar_lote(
        self, items: list[tuple[UUID, str]], user_id: UUID
    ) -> None:
        """Indexa varios mensajes del mismo usuario en UNA sola llamada de
        embeddings. Juntar el mensaje del usuario y la respuesta del asistente en
        un lote reduce las peticiones al proveedor (menos 429 en el free tier de
        Voyage). `items` = [(message_id, texto), ...]. Corre en background tras
        responder: tolera cualquier fallo sin romper nada."""
        pares = [(mid, t) for mid, t in items if t and t.strip()]
        if not pares:
            return
        try:
            vecs = await self._embedder.embed([t for _, t in pares], tipo="document")
            for (mid, _), vec in zip(pares, vecs):
                await asyncio.to_thread(
                    self._repo.save_message_embedding, mid, user_id, vec
                )
        except Exception:
            ids = ", ".join(str(mid) for mid, _ in pares)
            log.warning("No se pudo indexar el lote de mensajes (%s) en la memoria", ids, exc_info=True)

    # --- internos -------------------------------------------------------------
    async def _solo_hechos(self, user_id: UUID) -> list[UserFact]:
        return (await self._safe(self._repo.get_user_facts, user_id, self._max_hechos)) or []

    @staticmethod
    async def _safe(fn, *args):
        """Corre una llamada síncrona del repo en un hilo, tragándose el fallo
        (devuelve None) — un error de la memoria no puede tumbar la respuesta."""
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception:
            log.warning("Consulta de memoria falló (%s); se ignora", getattr(fn, "__name__", fn), exc_info=True)
            return None


def render_memoria(recuerdos: list[Recuerdo], hechos: list[UserFact]) -> Optional[str]:
    """Arma el bloque de sistema con la memoria recuperada, o None si no hay
    nada. Se inyecta como bloque dinámico (no cacheado) del agente: es texto,
    no turnos falsos del historial, para no confundir el orden conversacional."""
    if not recuerdos and not hechos:
        return None
    partes: list[str] = []
    if hechos:
        lineas = "\n".join(f"- {f.contenido}" for f in hechos)
        partes.append(
            "LO QUE SABES DEL USUARIO (de conversaciones pasadas; úsalo con "
            "naturalidad, no lo recites):\n" + lineas
        )
    if recuerdos:
        lineas = "\n".join(_linea_recuerdo(r) for r in recuerdos)
        partes.append(
            "RECUERDOS RELEVANTES a este mensaje (fragmentos de conversaciones "
            "anteriores, por si dan contexto; NO son el mensaje actual):\n" + lineas
        )
    return "\n\n".join(partes)


def _linea_recuerdo(r: Recuerdo) -> str:
    if r.origen == "resumen":
        return f"- (resumen) {r.contenido}"
    quien = {"user": "el usuario", "assistant": "tú"}.get(r.rol or "", "alguien")
    return f"- ({quien}) {r.contenido}"
