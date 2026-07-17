"""Parseo del comprobante electrónico del SRI en XML (plan de documentos, E2).

Sin dependencias externas (`xml.etree`): el XML que el SRI autoriza y envía por
correo YA trae todo estructurado — clave de acceso, emisor, fecha, total — así
que se extrae por script, sin gastar un token ni arriesgar una lectura errónea.
La clave de acceso se valida offline con el módulo 11 (reutiliza sri.py).

El XML del SRI llega en dos formas y se manejan ambas:
  1. Envuelto: <autorizacion><comprobante> con el comprobante real como CDATA
     (un string XML dentro del elemento) o como elementos anidados.
  2. Directo: la raíz es <factura> / <notaCredito> / <comprobanteRetencion>.

Regla de oro (como todo lo de documentos): ante cualquier XML que no se pueda
parsear como comprobante del SRI, devuelve None y el llamador lo trata como un
respaldo cualquiera — nunca revienta el pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from app.application.sri import validar_modulo11

log = logging.getLogger("e5.factura_xml")

#: Raíces de comprobante que sabemos leer (tag → etiqueta legible).
_TAGS_COMPROBANTE = {
    "factura": "factura",
    "notaCredito": "nota de crédito",
    "notaDebito": "nota de débito",
    "comprobanteRetencion": "comprobante de retención",
    "liquidacionCompra": "liquidación de compra",
}


@dataclass(frozen=True)
class FacturaXML:
    clave_acceso: str
    clave_valida: bool  # ¿pasa el módulo 11?
    tipo: str  # 'factura', 'nota de crédito', …
    emisor_ruc: str
    emisor_nombre: str
    fecha_emision: date | None
    total: Decimal | None


def parsear_factura_sri(contenido: bytes) -> FacturaXML | None:
    """XML del SRI (bytes) → FacturaXML, o None si no es un comprobante legible."""
    try:
        root = ET.fromstring(contenido)
    except ET.ParseError:
        log.info("XML no parseable como comprobante SRI.")
        return None

    interno = _documento_interno(root)
    if interno is None:
        return None

    info_trib = interno.find("infoTributaria")
    clave = _texto(info_trib, "claveAcceso") or _texto(root, "numeroAutorizacion")
    if not clave or len(clave) != 49 or not clave.isdigit():
        return None

    return FacturaXML(
        clave_acceso=clave,
        clave_valida=validar_modulo11(clave),
        tipo=_TAGS_COMPROBANTE.get(interno.tag, "comprobante"),
        emisor_ruc=_texto(info_trib, "ruc") or "",
        emisor_nombre=(
            _texto(info_trib, "razonSocial")
            or _texto(info_trib, "nombreComercial")
            or ""
        ),
        fecha_emision=_fecha(interno),
        total=_total(interno),
    )


def _documento_interno(root: ET.Element) -> ET.Element | None:
    """Ubica el <factura>/<notaCredito>/… real, venga directo, anidado o en el
    CDATA de <comprobante>."""
    if root.tag in _TAGS_COMPROBANTE:
        return root
    comp = root.find(".//comprobante")
    if comp is not None:
        if comp.text and "<" in comp.text:
            try:
                anidado = ET.fromstring(comp.text.strip())
                if anidado.tag in _TAGS_COMPROBANTE:
                    return anidado
            except ET.ParseError:
                pass
        for hijo in comp:
            if hijo.tag in _TAGS_COMPROBANTE:
                return hijo
    for tag in _TAGS_COMPROBANTE:
        el = root.find(f".//{tag}")
        if el is not None:
            return el
    return None


def _texto(elemento: ET.Element | None, tag: str) -> str | None:
    if elemento is None:
        return None
    hijo = elemento.find(f".//{tag}")
    return hijo.text.strip() if hijo is not None and hijo.text else None


def _fecha(interno: ET.Element) -> date | None:
    """fechaEmision del SRI viene como dd/mm/aaaa (a veces con hora detrás)."""
    crudo = (
        _texto(interno, "fechaEmision")
        or _texto(interno, "fechaEmisionDocSustento")
        or ""
    )
    crudo = crudo.split()[0] if crudo else ""
    for sep in ("/", "-"):
        partes = crudo.split(sep)
        if len(partes) == 3 and all(p.isdigit() for p in partes):
            d, m, a = (int(p) for p in partes)
            if a < 100:  # por si viniera aa
                a += 2000
            try:
                return date(a, m, d)
            except ValueError:
                return None
    return None


def _total(interno: ET.Element) -> Decimal | None:
    crudo = (
        _texto(interno, "importeTotal")  # factura
        or _texto(interno, "valorModificacion")  # nota de crédito/débito
        or _texto(interno, "importeTotalComprobantesReembolso")
    )
    if not crudo:
        return None
    try:
        return Decimal(crudo.replace(",", "."))
    except InvalidOperation:
        return None
