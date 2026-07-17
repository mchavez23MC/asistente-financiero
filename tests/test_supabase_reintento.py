"""Reintento de desconexiones transitorias de PostgREST (supabase_repo).

Cubre el fix del webhook 500: supabase-py reutiliza un httpx.Client HTTP/2 de
larga vida y Supabase cierra las conexiones ociosas, así que el primer uso tras
el cierre lanza RemoteProtocolError('Server disconnected'). Antes eso tumbaba el
webhook; ahora la consulta se reintenta a nivel de una sola petición HTTP.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.persistence.supabase_repo import (
    SupabaseRepository,
    _con_reintento_red,
)


def test_reintenta_y_tiene_exito_tras_desconexion_transitoria():
    llamadas = {"n": 0}

    def flaky(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return "OK"

    envuelto = _con_reintento_red(flaky, reintentos=2)
    assert envuelto() == "OK"
    assert llamadas["n"] == 2  # falló una vez, reintentó y pasó


def test_propaga_el_error_tras_agotar_los_reintentos():
    llamadas = {"n": 0}

    def siempre_falla(*args, **kwargs):
        llamadas["n"] += 1
        raise httpx.ConnectError("boom")

    envuelto = _con_reintento_red(siempre_falla, reintentos=2)
    with pytest.raises(httpx.ConnectError):
        envuelto()
    assert llamadas["n"] == 3  # intento inicial + 2 reintentos


def test_no_reintenta_errores_no_transitorios():
    llamadas = {"n": 0}

    def error_de_valor(*args, **kwargs):
        llamadas["n"] += 1
        raise ValueError("no es de red")

    envuelto = _con_reintento_red(error_de_valor, reintentos=2)
    with pytest.raises(ValueError):
        envuelto()
    assert llamadas["n"] == 1  # se propaga de inmediato, sin reintentar


def test_repo_instala_el_reintento_en_la_sesion_de_postgrest():
    # create_client no hace red en la init; solo comprobamos el cableado.
    repo = SupabaseRepository("https://dummy.supabase.co", "dummy-key")
    session = repo._db.postgrest.session
    # session.request quedó envuelto por _con_reintento_red (functools.wraps
    # expone el callable original en __wrapped__).
    assert hasattr(session.request, "__wrapped__")
