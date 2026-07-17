"""Login P0 (plan-paginas, módulo 01): validación estricta de celular EC
(cierra el bug de mandar el OTP a otro país — S1) y guard de AUTH_DEMO_OTP en
producción (S4).
"""

import pytest
from fastapi import HTTPException

from app.interfaces.api.webapp_api import _normalizar_ec, _telefono_valido
from app.main import _parece_produccion


# --- normalización EC (los 7 casos de la tabla §3 del plan) ------------------
def test_normaliza_formatos_validos_ec():
    assert _normalizar_ec("0987654321") == "+593987654321"
    assert _normalizar_ec("987654321") == "+593987654321"
    assert _normalizar_ec("+593987654321") == "+593987654321"
    assert _normalizar_ec("593987654321") == "+593987654321"
    assert _normalizar_ec("00593987654321") == "+593987654321"
    assert _normalizar_ec("098 765 4321") == "+593987654321"  # con espacios


def test_rechaza_numero_de_otro_pais():
    # El caso de la captura: '+958628665' NO debe mandar OTP a Myanmar (+95).
    assert _normalizar_ec("+14155550100") is None  # EEUU
    assert _normalizar_ec("+34600112233") is None  # España


def test_rechaza_no_celular():
    assert _normalizar_ec("022501234") is None  # fijo Quito (empieza en 2)
    assert _normalizar_ec("98765") is None  # muy corto
    assert _normalizar_ec("") is None


def test_telefono_valido_lanza_422():
    with pytest.raises(HTTPException) as exc:
        _telefono_valido("+14155550100")
    assert exc.value.status_code == 422


def test_telefono_valido_devuelve_normalizado():
    assert _telefono_valido("0987654321") == "+593987654321"


# --- guard de AUTH_DEMO_OTP en producción (S4) -------------------------------
def test_parece_produccion():
    assert _parece_produccion("https://asistente-financiero.up.railway.app") is True
    assert _parece_produccion("") is False
    assert _parece_produccion("https://abc.ngrok-free.dev") is False
    assert _parece_produccion("http://localhost:8080") is False


def test_create_app_aborta_con_demo_otp_en_produccion():
    from dataclasses import replace

    from app.infra.config import Settings
    from app.main import create_app

    base = Settings.from_env()
    peligrosa = replace(
        base, auth_demo_otp="424242", public_base_url="https://miapp.up.railway.app"
    )
    with pytest.raises(RuntimeError, match="AUTH_DEMO_OTP"):
        create_app(peligrosa)
