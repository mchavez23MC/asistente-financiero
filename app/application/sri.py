"""Detección y validación offline de comprobantes electrónicos del SRI.

Etapa 1 del plan de documentos: sobre el texto ya extraído de un PDF
(pdf_texto.py), busca la clave de acceso de 49 dígitos que llevan todos los
comprobantes electrónicos ecuatorianos (facturas RIDE, notas de crédito,
retenciones) y la valida con el algoritmo módulo 11 del SRI — pura aritmética,
sin red ni IA. Una clave que pasa el checksum identifica el documento de forma
determinista y sus campos (fecha, RUC, tipo) vienen codificados en la propia
clave: extracción verificada matemáticamente.

Estructura de la clave (49 dígitos):
  [0:8]   fecha de emisión ddmmaaaa
  [8:10]  tipo de comprobante (01=factura, 04=nota crédito, …)
  [10:23] RUC del emisor
  [23]    ambiente (1=pruebas, 2=producción)
  [24:39] serie + secuencial
  [39:47] código numérico
  [47]    tipo de emisión
  [48]    dígito verificador (módulo 11)

Esto NO dice la dirección del dinero (¿el usuario compró o emitió?): esa la
confirma siempre el usuario — regla del system prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: Tipos de comprobante del SRI (campo [8:10] de la clave).
_TIPOS_COMPROBANTE = {
    "01": "factura",
    "03": "liquidación de compra",
    "04": "nota de crédito",
    "05": "nota de débito",
    "06": "guía de remisión",
    "07": "comprobante de retención",
}

#: 49 dígitos seguidos; en los RIDE la clave a veces se imprime partida por
#: espacios o saltos de línea, así que también se busca sobre una copia del
#: texto con los separadores entre dígitos colapsados.
_RE_CLAVE = re.compile(r"\d{49}")
_RE_SEPARADOR_ENTRE_DIGITOS = re.compile(r"(?<=\d)[\s.-]+(?=\d)")


@dataclass(frozen=True)
class ComprobanteSRI:
    clave_acceso: str
    valida: bool  # ¿el dígito verificador pasa el módulo 11?
    tipo: str  # 'factura', 'nota de crédito', … o 'desconocido'
    fecha_emision: date | None
    ruc_emisor: str
    ambiente: str  # 'producción' | 'pruebas' | 'desconocido'


def validar_modulo11(clave: str) -> bool:
    """Checksum oficial del SRI: pesos 2..7 cíclicos de derecha a izquierda
    sobre los primeros 48 dígitos; dv = 11 - (suma % 11), con 11→0 y 10→1."""
    if len(clave) != 49 or not clave.isdigit():
        return False
    suma = 0
    peso = 2
    for digito in reversed(clave[:48]):
        suma += int(digito) * peso
        peso = peso + 1 if peso < 7 else 2
    dv = 11 - (suma % 11)
    if dv == 11:
        dv = 0
    elif dv == 10:
        dv = 1
    return dv == int(clave[48])


def _campos(clave: str, valida: bool) -> ComprobanteSRI:
    try:
        fecha = date(int(clave[4:8]), int(clave[2:4]), int(clave[0:2]))
    except ValueError:
        fecha = None
    return ComprobanteSRI(
        clave_acceso=clave,
        valida=valida,
        tipo=_TIPOS_COMPROBANTE.get(clave[8:10], "desconocido"),
        fecha_emision=fecha,
        ruc_emisor=clave[10:23],
        ambiente={"1": "pruebas", "2": "producción"}.get(clave[23], "desconocido"),
    )


def detectar_comprobante_sri(texto: str) -> ComprobanteSRI | None:
    """Busca claves de acceso en el texto y devuelve el mejor hallazgo:
    una clave VÁLIDA si existe; si solo hay candidatas que fallan el checksum,
    la primera de ellas con valida=False (señal de alerta: documento con
    pinta de comprobante pero clave que no cuadra). None = nada parecido."""
    candidatas: list[str] = []
    for haystack in (texto, _RE_SEPARADOR_ENTRE_DIGITOS.sub("", texto)):
        for m in _RE_CLAVE.finditer(haystack):
            if m.group() not in candidatas:
                candidatas.append(m.group())
    if not candidatas:
        return None
    for clave in candidatas:
        if validar_modulo11(clave):
            return _campos(clave, valida=True)
    return _campos(candidatas[0], valida=False)


def nota_para_agente(c: ComprobanteSRI) -> str:
    """Bloque de texto que se le inyecta al agente junto al contenido del PDF,
    para que trate los datos verificados como confiables (o desconfíe si el
    checksum falló). La dirección del dinero sigue siendo del usuario."""
    if c.valida:
        fecha = c.fecha_emision.isoformat() if c.fecha_emision else "ilegible"
        return (
            "[VALIDACIÓN AUTOMÁTICA DEL SISTEMA: este documento contiene una "
            f"clave de acceso del SRI matemáticamente VÁLIDA. Tipo: {c.tipo}. "
            f"Fecha de emisión según la clave: {fecha}. RUC del emisor: "
            f"{c.ruc_emisor}. Ambiente: {c.ambiente}. Estos campos vienen "
            "verificados; prefiérelos si el texto del documento se contradice. "
            "La validación NO indica si la plata entra o sale: eso confírmalo "
            "con el usuario como siempre.]"
        )
    return (
        "[VALIDACIÓN AUTOMÁTICA DEL SISTEMA: el documento aparenta ser un "
        "comprobante electrónico del SRI pero su clave de acceso NO pasa la "
        "validación del dígito verificador. Puede ser un error de lectura o un "
        "documento alterado: NO registres nada todavía; dile al usuario lo que "
        "viste y pídele confirmar los datos o reenviar el documento.]"
    )
