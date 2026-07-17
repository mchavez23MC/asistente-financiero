"""EmbeddingProvider sobre Voyage AI — memoria semántica (Parte A del plan).

Voyage es el socio de embeddings recomendado por Anthropic. Se usa su API REST
directamente con httpx (ya es dependencia del proyecto), sin SDK extra. El
modelo por defecto es `voyage-3.5-lite` (barato, 1024 dims) — la dimensión debe
coincidir con `vector(1024)` del schema SQL.

Este adaptador es OPCIONAL en el composition root: si no hay VOYAGE_API_KEY, no
se instancia y la memoria semántica queda apagada (el asistente usa solo la
ventana reciente de mensajes, como antes de esta feature).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("e5.embeddings")

_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageEmbeddingProvider:
    #: Dimensión del modelo por defecto (voyage-3.5-lite). Coincide con el schema.
    dimensiones = 1024

    def __init__(
        self,
        api_key: str,
        model: str = "voyage-3.5-lite",
        timeout_s: float = 10.0,
        reintentos_indexado: int = 2,
        backoff_cap_s: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        # Reintentos ante 429 SOLO para el indexado en background (tipo
        # 'document'); la consulta ('query') corre antes de responderle al
        # usuario, así que falla rápido para no sumar latencia a la respuesta.
        self._reintentos_indexado = reintentos_indexado
        self._backoff_cap_s = backoff_cap_s

    async def embed(
        self, textos: list[str], tipo: str = "document"
    ) -> list[list[float]]:
        """Vectoriza un lote. `tipo` mapea a `input_type` de Voyage ('query' para
        la consulta a buscar, 'document' para lo que se indexa). Devuelve un
        vector por texto, en orden. Lote vacío → lista vacía sin llamar al API.

        Ante 429 (rate limit del free tier de Voyage) reintenta con backoff
        —respetando `Retry-After`— solo en el indexado en background; la consulta
        falla de inmediato (la memoria es opcional y no debe demorar la
        respuesta)."""
        if not textos:
            return []
        input_type = "query" if tipo == "query" else "document"
        reintentos = self._reintentos_indexado if input_type == "document" else 0
        payload = {
            "input": textos,
            "model": self._model,
            "input_type": input_type,
            "output_dimension": self.dimensiones,
        }
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            for intento in range(reintentos + 1):
                resp = await client.post(
                    _VOYAGE_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                if resp.status_code == 429 and intento < reintentos:
                    await asyncio.sleep(self._espera_tras_429(resp, intento))
                    continue
                resp.raise_for_status()
                data = resp.json()
                # La respuesta preserva el orden de entrada, pero se ordena por
                # `index` por si acaso, para no desalinear vectores con textos.
                filas = sorted(data["data"], key=lambda d: d["index"])
                return [fila["embedding"] for fila in filas]

    def _espera_tras_429(self, resp: httpx.Response, intento: int) -> float:
        """Segundos a esperar antes de reintentar un 429. Respeta `Retry-After`
        si viene (segundos); si no, backoff exponencial. Se limita a
        `backoff_cap_s` para no colgar el indexado indefinidamente."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self._backoff_cap_s)
            except ValueError:
                pass
        return min(2.0 ** intento, self._backoff_cap_s)
