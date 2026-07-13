"""Prueba de humo de los contratos congelados (Fase 1).

No prueba lógica de negocio (aún no existe) — verifica que los 3 contratos
importan, validan y respetan sus invariantes clave.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain import ports
from app.domain.models import (
    IncomingMessage,
    Rol,
    Ticket,
    Transaction,
    TransactionStatus,
    User,
)
from app.domain.tools import TOOL_NAMES, TOOLS


# --- modelos ----------------------------------------------------------------
def test_user_valida_e164():
    u = User(telefono="+50370000000", nombre="Ana")
    assert u.telefono == "+50370000000"
    assert u.tiene_consentimiento is False


def test_user_rechaza_telefono_invalido():
    with pytest.raises(ValidationError):
        User(telefono="70000000")  # sin '+' ni código de país


def test_user_con_consentimiento():
    u = User(telefono="+50370000000", consentimiento_at=datetime.now(timezone.utc))
    assert u.tiene_consentimiento is True


def test_transaction_pendiente_por_defecto():
    t = Transaction(user_id=uuid4())
    assert t.status == TransactionStatus.PENDIENTE_CONFIRMACION
    assert t.monto is None  # incompleta hasta confirmar (H1)


def test_transaction_rechaza_monto_negativo():
    with pytest.raises(ValidationError):
        Transaction(user_id=uuid4(), monto=Decimal("-5"))


def test_incoming_message_formato_canonico():
    im = IncomingMessage(canal="whatsapp", telefono="+50370000000", texto="hola")
    assert im.canal == "whatsapp"
    assert im.raw == {}


def test_ticket_defaults():
    tk = Ticket(user_id=uuid4(), motivo="reclamo", contexto="cliente pide reembolso")
    assert tk.estado == "abierto"
    assert tk.prioridad == "media"


# --- puertos ----------------------------------------------------------------
def test_puertos_existen():
    for nombre in (
        "ChannelAdapter",
        "LLMProvider",
        "AgentHandler",
        "AgentRegistry",
        "Guardrail",
        "Repository",
    ):
        assert hasattr(ports, nombre), f"Falta el puerto {nombre}"


# --- contrato de tools (T1) -------------------------------------------------
def test_set_de_tools():
    assert TOOL_NAMES == {
        "registrar_gasto",
        "registrar_ingreso",
        "configurar_ingreso_recurrente",
        "consultar_movimientos",
        "editar_transaccion",
        "eliminar_transaccion",
        "consultar_presupuesto",
        "configurar_presupuesto",
        "responder_soporte",
        "crear_ticket",
    }


def test_user_id_nunca_es_parametro_de_tool():
    """Invariante de seguridad §7.3.2: ninguna tool acepta user_id."""
    for tool in TOOLS:
        params = tool["input_schema"]["properties"].keys()
        assert "user_id" not in params, f"{tool['name']} expone user_id"
        assert "telefono" not in params, f"{tool['name']} expone telefono"


def test_tools_tienen_formato_anthropic():
    for tool in TOOLS:
        assert set(tool.keys()) >= {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"
