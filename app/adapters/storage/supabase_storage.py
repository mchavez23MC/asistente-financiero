"""Almacén de originales sobre Supabase Storage (plan de documentos, E1).

Implementa el puerto `DocumentStorage`. Sin lógica de negocio: subir, bajar,
firmar URL, borrar. Los errores se propagan (nunca `except: pass`): el caso de
uso decide qué hacer — regla "primero persistir, después interpretar": si esto
falla, NO se procesa nada y se pide reintento (fail-closed, riesgo R10).
"""

from __future__ import annotations

from supabase import Client, create_client


class SupabaseDocumentStorage:
    def __init__(self, url: str, key: str, bucket: str = "documentos") -> None:
        self._client: Client = create_client(url, key)
        self._bucket = bucket

    def verificar_bucket(self) -> None:
        """Falla con mensaje claro si el bucket no existe (guard de arranque:
        con DOCS_HABILITADO=true un deploy sin bucket debe ABORTAR, no fallar
        en runtime con el primer documento)."""
        nombres = {b.name for b in self._client.storage.list_buckets()}
        if self._bucket not in nombres:
            raise RuntimeError(
                f"El bucket de Storage '{self._bucket}' no existe en Supabase. "
                "Créalo (privado) desde el dashboard → Storage, o corre la "
                "migración db/migracion-documentos.sql y sigue sus pasos."
            )

    def guardar(self, path: str, contenido: bytes, content_type: str) -> None:
        self._client.storage.from_(self._bucket).upload(
            path, contenido, file_options={"content-type": content_type}
        )

    def leer(self, path: str) -> bytes:
        return self._client.storage.from_(self._bucket).download(path)

    def signed_url(self, path: str, expira_s: int = 600) -> str:
        res = self._client.storage.from_(self._bucket).create_signed_url(path, expira_s)
        return res["signedURL"]

    def borrar(self, path: str) -> None:
        self._client.storage.from_(self._bucket).remove([path])
