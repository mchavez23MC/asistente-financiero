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
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    async def embed(
        self, textos: list[str], tipo: str = "document"
    ) -> list[list[float]]:
        """Vectoriza un lote. `tipo` mapea a `input_type` de Voyage ('query' para
        la consulta a buscar, 'document' para lo que se indexa). Devuelve un
        vector por texto, en orden. Lote vacío → lista vacía sin llamar al API."""
        if not textos:
            return []
        input_type = "query" if tipo == "query" else "document"
        payload = {
            "input": textos,
            "model": self._model,
            "input_type": input_type,
            "output_dimension": self.dimensiones,
        }
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                _VOYAGE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        # La respuesta preserva el orden de entrada, pero se ordena por `index`
        # por si acaso, para no desalinear vectores con sus textos.
        filas = sorted(data["data"], key=lambda d: d["index"])
        return [fila["embedding"] for fila in filas]
