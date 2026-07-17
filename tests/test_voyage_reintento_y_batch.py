"""Mitigación de 429 de Voyage (Opción B):

1. El adaptador reintenta 429 con backoff SOLO en el indexado ('document'); la
   consulta ('query') falla rápido para no sumar latencia a la respuesta.
2. La memoria indexa el mensaje del usuario y la respuesta del asistente en UN
   solo lote → una llamada de embeddings en vez de dos.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.adapters.embeddings.voyage import VoyageEmbeddingProvider
from app.application.memoria import MemoriaSemantica
from app.domain.models import Message, Rol
from tests.test_memoria import FakeEmbedder
from tests.test_walking_skeleton import FakeRepo

_URL = "https://api.voyageai.com/v1/embeddings"
_REQ = httpx.Request("POST", _URL)


def _ok(n: int = 1) -> httpx.Response:
    data = [{"index": i, "embedding": [0.1] * 1024} for i in range(n)]
    return httpx.Response(200, request=_REQ, json={"data": data})


def _429() -> httpx.Response:
    return httpx.Response(429, request=_REQ, headers={"Retry-After": "0"})


async def test_indexado_reintenta_tras_429_y_tiene_exito():
    vp = VoyageEmbeddingProvider("k", reintentos_indexado=2, backoff_cap_s=0.01)
    post = AsyncMock(side_effect=[_429(), _ok()])
    with patch("httpx.AsyncClient.post", post):
        vecs = await vp.embed(["hola"], tipo="document")
    assert len(vecs) == 1
    assert post.await_count == 2  # 429 → reintento → 200


async def test_consulta_no_reintenta_y_propaga_429():
    vp = VoyageEmbeddingProvider("k", reintentos_indexado=2, backoff_cap_s=0.01)
    post = AsyncMock(side_effect=[_429(), _ok()])
    with patch("httpx.AsyncClient.post", post):
        with pytest.raises(httpx.HTTPStatusError):
            await vp.embed(["hola"], tipo="query")
    assert post.await_count == 1  # falla rápido, sin reintentar


async def test_indexado_agota_reintentos_y_propaga():
    vp = VoyageEmbeddingProvider("k", reintentos_indexado=2, backoff_cap_s=0.01)
    post = AsyncMock(side_effect=[_429(), _429(), _429()])
    with patch("httpx.AsyncClient.post", post):
        with pytest.raises(httpx.HTTPStatusError):
            await vp.embed(["hola"], tipo="document")
    assert post.await_count == 3  # intento inicial + 2 reintentos


def test_espera_respeta_retry_after_con_cap():
    vp = VoyageEmbeddingProvider("k", backoff_cap_s=5.0)
    assert vp._espera_tras_429(httpx.Response(429, request=_REQ, headers={"Retry-After": "3"}), 0) == 3.0
    assert vp._espera_tras_429(httpx.Response(429, request=_REQ, headers={"Retry-After": "999"}), 0) == 5.0
    assert vp._espera_tras_429(httpx.Response(429, request=_REQ), 0) == 1.0   # 2**0
    assert vp._espera_tras_429(httpx.Response(429, request=_REQ), 10) == 5.0  # 2**10 capado


async def test_indexar_lote_usa_una_sola_llamada_de_embeddings():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = repo.get_or_create_user("+50370000000", "Ana")
    m1 = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="pago la renta"))
    m2 = repo.save_message(Message(user_id=user.id, rol=Rol.ASISTENTE, contenido="anotado el cafe"))

    await MemoriaSemantica(repo, emb).indexar_lote(
        [(m1.id, "pago la renta"), (m2.id, "anotado el cafe")], user.id
    )

    assert len(emb.llamadas) == 1  # UNA sola llamada, no dos
    assert emb.llamadas[0][0] == ["pago la renta", "anotado el cafe"]
    assert m1.id in repo.message_embeddings
    assert m2.id in repo.message_embeddings


async def test_indexar_lote_ignora_textos_vacios():
    repo, emb = FakeRepo(), FakeEmbedder()
    user = repo.get_or_create_user("+50370000000", "Ana")
    m = repo.save_message(Message(user_id=user.id, rol=Rol.USUARIO, contenido="pago la renta"))

    await MemoriaSemantica(repo, emb).indexar_lote([(m.id, "   ")], user.id)

    assert emb.llamadas == []  # nada que indexar → no se llama al proveedor
