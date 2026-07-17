"""Consulta de respaldos guardados (plan de documentos, módulo 05).

`consultar_documentos` es la tool grounded que deja al usuario preguntarle a
Luca por sus documentos ("¿qué respaldos tengo de junio?", "¿tienes la factura
de Supermaxi?"). Igual que `consultar_movimientos`: el sistema lista desde la
tabla `documents`, Luca solo lo verbaliza — nunca inventa.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from app.domain.models import Document
from app.domain.ports import Repository

log = logging.getLogger("e5.documentos.consulta")

_TIPOS_VALIDOS = {
    "factura_sri",
    "retencion",
    "nota_credito",
    "transferencia",
    "planilla_servicio",
    "estado_cuenta",
    "rol_pagos",
    "voucher",
    "otro_respaldo",
}


def consultar_documentos(
    repo: Repository,
    user_id: UUID,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    tipo: Optional[str] = None,
    limite: int = 10,
) -> dict:
    try:
        limite = max(1, min(int(limite), 50))
    except (TypeError, ValueError):
        limite = 10
    if tipo not in _TIPOS_VALIDOS:
        tipo = None
    try:
        docs = repo.list_documents(
            user_id, desde=desde, hasta=hasta, tipo=tipo, limite=limite
        )
    except Exception:
        # Con DOCS_HABILITADO apagado la tabla puede no existir: la consulta se
        # degrada a "no hay respaldos" en vez de romper la respuesta del agente.
        log.warning("list_documents falló; se devuelve vacío", exc_info=True)
        return {"documentos": [], "cuantos": 0}
    return {"documentos": [_resumen(d) for d in docs], "cuantos": len(docs)}


def _resumen(d: Document) -> dict:
    return {
        "tipo": d.tipo_documento,
        "emisor": d.emisor_nombre,
        "fecha": d.fecha_emision.isoformat() if d.fecha_emision else None,
        "total": float(d.total) if d.total is not None else None,
        "archivo": d.filename,
    }
