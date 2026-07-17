"""Tests de la validación offline de comprobantes SRI (etapa 1 de documentos).

El dígito verificador de las claves sintéticas se calcula con una
implementación INDEPENDIENTE del módulo 11 (_dv_referencia), para que un bug
en la implementación real no se auto-valide.
"""

import base64

from app.application.agents.principal import _bloques_media
from app.application.sri import (
    detectar_comprobante_sri,
    nota_para_agente,
    validar_modulo11,
)
from app.domain.models import IncomingMessage, MediaItem
from tests.test_pdf_texto import _pdf_minimo


def _dv_referencia(cuerpo48: str) -> int:
    """Módulo 11 del SRI, escrito distinto a propósito (pesos generados por
    posición en vez de contador cíclico)."""
    pesos = [2 + (i % 6) for i in range(48)]  # derecha → izquierda
    suma = sum(int(d) * p for d, p in zip(reversed(cuerpo48), pesos))
    dv = 11 - (suma % 11)
    return {11: 0, 10: 1}.get(dv, dv)


def _clave(fecha="10072026", tipo="01", ruc="1790012345001", ambiente="2") -> str:
    cuerpo = fecha + tipo + ruc + ambiente + "001001" + "000123456" + "12345678" + "1"
    assert len(cuerpo) == 48
    return cuerpo + str(_dv_referencia(cuerpo))


# --- módulo 11 ----------------------------------------------------------------
def test_clave_bien_formada_valida():
    assert validar_modulo11(_clave()) is True


def test_digito_alterado_invalida():
    clave = _clave()
    alterada = clave[:20] + str((int(clave[20]) + 1) % 10) + clave[21:]
    assert validar_modulo11(alterada) is False


def test_largo_o_no_numerico_invalida():
    assert validar_modulo11("123") is False
    assert validar_modulo11("x" * 49) is False


# --- detección en texto -------------------------------------------------------
def test_detecta_clave_valida_y_sus_campos():
    texto = f"FACTURA No. 001-001-000123456\nCLAVE DE ACCESO: {_clave()}\nTOTAL: $32.50"
    c = detectar_comprobante_sri(texto)
    assert c is not None and c.valida
    assert c.tipo == "factura"
    assert c.fecha_emision is not None and c.fecha_emision.isoformat() == "2026-07-10"
    assert c.ruc_emisor == "1790012345001"
    assert c.ambiente == "producción"


def test_detecta_clave_partida_por_espacios():
    clave = _clave()
    texto = f"CLAVE DE ACCESO\n{clave[:25]} {clave[25:]}\n"
    c = detectar_comprobante_sri(texto)
    assert c is not None and c.valida and c.clave_acceso == clave


def test_clave_con_checksum_roto_se_reporta_invalida():
    clave = _clave()[:-1] + str((int(_clave()[-1]) + 1) % 10)
    c = detectar_comprobante_sri(f"clave: {clave}")
    assert c is not None and c.valida is False
    assert "NO pasa la validación" in nota_para_agente(c)


def test_texto_sin_clave_devuelve_none():
    assert detectar_comprobante_sri("Recibo del súper por $10, gracias.") is None


# --- integración: la nota viaja con el texto del PDF -------------------------
def test_pdf_de_factura_sri_lleva_nota_de_validacion():
    texto_factura = f"FACTURA Supermaxi TOTAL 32.50 CLAVE {_clave()} " * 2
    pdf_b64 = base64.b64encode(_pdf_minimo(texto_factura)).decode()
    incoming = IncomingMessage(
        canal="whatsapp",
        telefono="+50370000000",
        texto="",
        media=[
            MediaItem(
                content_type="application/pdf",
                url="https://x",
                data_base64=pdf_b64,
                filename="factura.pdf",
            )
        ],
    )
    bloques = _bloques_media(incoming)
    assert "VALIDACIÓN AUTOMÁTICA DEL SISTEMA" in bloques[0]["text"]
    assert "matemáticamente VÁLIDA" in bloques[0]["text"]
