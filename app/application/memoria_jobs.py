"""Jobs de memoria de largo plazo (Parte A del plan — A3 y A4).

Dos tareas periódicas que el scheduler corre fuera de la ruta del mensaje:

  - `extraer_hechos_usuario` (A3): de las conversaciones recientes, extrae con un
    LLM los HECHOS estables del usuario (preferencias, hábitos, datos que se
    repiten) y los guarda en `user_facts`, deduplicando por similitud. No guarda
    detalles de una transacción puntual (eso vive en `transactions`).

  - `resumir_conversaciones_inactivas` (A4): cuando un usuario lleva un rato sin
    escribir, resume en 2-4 frases lo hablado desde el último resumen y lo guarda
    con su embedding, para que sea recuperable como memoria episódica.

Ambas son funciones puras (reciben puertos, devuelven un conteo), igual que
`revisar_presupuestos`, para poder probarlas con fakes. Cada usuario se procesa
de forma aislada: un fallo con uno no detiene a los demás.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.domain.models import ConversationSummary, Message, UserFact
from app.domain.ports import EmbeddingProvider, LLMProvider, Repository

log = logging.getLogger("e5.memoria.jobs")

# Umbral de deduplicación de hechos: si un candidato se parece a un hecho ya
# guardado por encima de esto, se actualiza ese en vez de insertar uno nuevo.
_DEDUP_HECHOS = 0.90

_PROMPT_HECHOS = """\
Extraes HECHOS ESTABLES sobre un usuario de un asistente financiero personal, a \
partir de un fragmento de su conversación. Un hecho estable es algo que seguirá \
siendo útil recordar en futuras conversaciones: una preferencia ("prefiere \
respuestas cortas"), un hábito ("suele almorzar fuera"), o un dato recurrente \
("le pagan el 30", "su sueldo base es 450").

NO extraigas: montos de un gasto puntual, saldos, ni nada efímero (eso ya se \
guarda aparte). NO inventes: si no hay hechos claros, devuelve una lista vacía. \
Máximo 5 hechos, cada uno en una frase corta y en tercera persona.

Devuelve SOLO JSON válido:
{"hechos": [{"tipo": "preferencia|habito|dato|otro", "contenido": "..."}]}"""

_PROMPT_RESUMEN = """\
Resume en 2 a 4 frases lo esencial de esta conversación entre un usuario y su \
asistente financiero: qué hizo o pidió el usuario y cualquier compromiso o dato \
importante que quede pendiente. Escribe en tercera persona, sin saludos.

Devuelve SOLO JSON válido: {"resumen": "..."}"""


def _texto_conversacion(mensajes: list[Message], limite_chars: int = 4000) -> str:
    """Serializa mensajes a 'usuario:/asistente:' para el prompt, acotado."""
    lineas = []
    for m in mensajes:
        rol = m.rol if isinstance(m.rol, str) else m.rol.value
        quien = "usuario" if rol == "user" else "asistente"
        lineas.append(f"{quien}: {m.contenido}")
    texto = "\n".join(lineas)
    return texto[-limite_chars:]


def _parse_json(texto: str | None) -> dict:
    try:
        return json.loads(texto or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


async def extraer_hechos_usuario(
    repo: Repository,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    ventana_horas: int = 24,
    ahora: datetime | None = None,
    max_mensajes: int = 30,
) -> int:
    """Extrae y guarda hechos de los usuarios activos en la última ventana.
    Devuelve cuántos hechos se insertaron o actualizaron."""
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(hours=ventana_horas)
    guardados = 0
    for user_id in repo.usuarios_activos_desde(desde):
        try:
            guardados += await _hechos_de_un_usuario(
                repo, llm, embedder, user_id, max_mensajes
            )
        except Exception:
            log.warning("Extracción de hechos falló para %s", user_id, exc_info=True)
    return guardados


async def _hechos_de_un_usuario(
    repo: Repository,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    user_id: UUID,
    max_mensajes: int,
) -> int:
    mensajes = repo.get_last_n_messages(user_id, max_mensajes)
    if not mensajes:
        return 0
    resp = await llm.complete(
        messages=[{"role": "user", "content": _texto_conversacion(mensajes)}],
        system=_PROMPT_HECHOS,
    )
    candidatos = _parse_json(resp.texto).get("hechos", [])
    guardados = 0
    for c in candidatos:
        contenido = (c.get("contenido") or "").strip()
        if not contenido:
            continue
        tipo = c.get("tipo", "otro")
        vec = (await embedder.embed([contenido], tipo="document"))[0]
        # Dedup: ¿ya existe un hecho casi igual? Si sí, se actualiza ese id.
        similares = repo.match_user_facts(user_id, vec, match_count=1)
        fact_id = None
        if similares and similares[0][2] >= _DEDUP_HECHOS:
            fact_id = similares[0][0]
        repo.upsert_user_fact(
            UserFact(id=fact_id, user_id=user_id, tipo=tipo, contenido=contenido),
            embedding=vec,
        )
        guardados += 1
    return guardados


async def resumir_conversaciones_inactivas(
    repo: Repository,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    inactividad_horas: int = 6,
    ventana_horas: int = 72,
    ahora: datetime | None = None,
    max_mensajes: int = 50,
) -> int:
    """Resume la conversación de cada usuario que lleva `inactividad_horas` sin
    escribir y tiene mensajes nuevos desde su último resumen. Devuelve cuántos
    resúmenes se crearon."""
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(hours=ventana_horas)
    creados = 0
    for user_id in repo.usuarios_activos_desde(desde):
        try:
            if await _resumir_un_usuario(
                repo, llm, embedder, user_id, inactividad_horas, ahora, max_mensajes
            ):
                creados += 1
        except Exception:
            log.warning("Resumen falló para %s", user_id, exc_info=True)
    return creados


async def _resumir_un_usuario(
    repo: Repository,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    user_id: UUID,
    inactividad_horas: int,
    ahora: datetime,
    max_mensajes: int,
) -> bool:
    mensajes = repo.get_last_n_messages(user_id, max_mensajes)
    if not mensajes:
        return False
    ultimo_ts = mensajes[-1].timestamp
    # Sesión aún activa: el usuario escribió hace poco → todavía no se resume.
    if ahora - ultimo_ts < timedelta(hours=inactividad_horas):
        return False
    # Solo lo nuevo desde el último resumen (idempotencia).
    corte = repo.get_ultimo_resumen_ts(user_id)
    nuevos = [m for m in mensajes if corte is None or m.timestamp > corte]
    if not nuevos:
        return False
    resp = await llm.complete(
        messages=[{"role": "user", "content": _texto_conversacion(nuevos)}],
        system=_PROMPT_RESUMEN,
    )
    resumen = (_parse_json(resp.texto).get("resumen") or "").strip()
    if not resumen:
        return False
    vec = (await embedder.embed([resumen], tipo="document"))[0]
    repo.save_conversation_summary(
        ConversationSummary(
            user_id=user_id,
            resumen=resumen,
            desde_ts=nuevos[0].timestamp,
            hasta_ts=nuevos[-1].timestamp,
        ),
        embedding=vec,
    )
    return True
