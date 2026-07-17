"""Tests del parseo del XML del SRI (plan de documentos, E2).

Reusa `_clave` de test_sri (clave con dígito verificador correcto calculado por
una implementación independiente del módulo 11).
"""

from datetime import date
from decimal import Decimal

from app.application.documents.factura_xml import parsear_factura_sri
from tests.test_sri import _clave

_FACTURA_INTERNA = """<factura id="comprobante" version="1.1.0">
  <infoTributaria>
    <ambiente>2</ambiente>
    <razonSocial>SUPERMERCADOS LA FAVORITA C.A.</razonSocial>
    <ruc>1790016919001</ruc>
    <claveAcceso>{clave}</claveAcceso>
  </infoTributaria>
  <infoFactura>
    <fechaEmision>10/07/2026</fechaEmision>
    <importeTotal>32.50</importeTotal>
  </infoFactura>
</factura>"""


def _xml_directo(clave: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>' + _FACTURA_INTERNA.format(clave=clave)).encode()


def _xml_envuelto(clave: str) -> bytes:
    interna = _FACTURA_INTERNA.format(clave=clave)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<autorizacion>"
        "<estado>AUTORIZADO</estado>"
        f"<numeroAutorizacion>{clave}</numeroAutorizacion>"
        "<ambiente>PRODUCCION</ambiente>"
        f"<comprobante><![CDATA[{interna}]]></comprobante>"
        "</autorizacion>"
    ).encode()


def test_parsea_factura_directa():
    clave = _clave()
    f = parsear_factura_sri(_xml_directo(clave))
    assert f is not None
    assert f.clave_acceso == clave and f.clave_valida
    assert f.tipo == "factura"
    assert f.emisor_ruc == "1790016919001"
    assert "FAVORITA" in f.emisor_nombre
    assert f.fecha_emision == date(2026, 7, 10)
    assert f.total == Decimal("32.50")


def test_parsea_factura_envuelta_en_autorizacion_cdata():
    clave = _clave()
    f = parsear_factura_sri(_xml_envuelto(clave))
    assert f is not None
    assert f.clave_acceso == clave and f.clave_valida
    assert f.total == Decimal("32.50")
    assert f.fecha_emision == date(2026, 7, 10)


def test_clave_invalida_se_reporta():
    clave_mala = _clave()[:-1] + str((int(_clave()[-1]) + 1) % 10)
    f = parsear_factura_sri(_xml_directo(clave_mala))
    assert f is not None and f.clave_valida is False


def test_xml_no_sri_devuelve_none():
    assert parsear_factura_sri(b"<factura/>") is None
    assert parsear_factura_sri(b"<cualquierCosa><x>1</x></cualquierCosa>") is None


def test_xml_corrupto_devuelve_none():
    assert parsear_factura_sri(b"no soy xml <<<") is None
