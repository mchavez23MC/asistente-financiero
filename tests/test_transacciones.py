"""Fase 10 — historial, correcciones, eliminación y duplicados.

Cubre las tools consultar_movimientos / editar_transaccion / eliminar_transaccion
y la detección de duplicados de registrar_gasto/registrar_ingreso, sobre el
FakeRepo en memoria. También el manejo de fallo de tool en el agente principal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.application.agents.gasto import registrar_gasto
from app.application.agents.ingreso import registrar_ingreso
from app.application.agents.transacciones import (
    consultar_movimientos,
    editar_transaccion,
    eliminar_transaccion,
)
from app.domain.models import Transaction

from tests.test_walking_skeleton import FakeRepo


def _user(repo: FakeRepo):
    return repo.get_or_create_user("+50370000000", "Ana")


# --- consultar_movimientos -------------------------------------------------------
def test_consultar_movimientos_lista_los_ultimos_primero():
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=10, categoria="comida", comercio="pupusería")
    registrar_gasto(repo, u.id, monto=20, categoria="transporte")
    r = consultar_movimientos(repo, u.id, limite=5)

    assert r["cuantos"] == 2
    assert r["movimientos"][0]["monto"] == 20.0  # más reciente primero
    assert r["movimientos"][1]["comercio_o_fuente"] == "pupusería"
    assert all("transaction_id" in m for m in r["movimientos"])


def test_consultar_movimientos_filtra_por_tipo_y_excluye_pendientes_y_anuladas():
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=10, categoria="comida")
    registrar_ingreso(repo, u.id, monto=450, categoria="Salario")
    registrar_gasto(repo, u.id, categoria="ropa")  # pendiente (sin monto)
    r_gastos = consultar_movimientos(repo, u.id, tipo="gasto")
    assert [m["tipo"] for m in r_gastos["movimientos"]] == ["gasto"]

    # Anulada deja de listarse.
    tid = r_gastos["movimientos"][0]["transaction_id"]
    eliminar_transaccion(repo, u.id, transaction_id=tid, confirmado=True)
    assert consultar_movimientos(repo, u.id, tipo="gasto")["cuantos"] == 0


# --- editar_transaccion ------------------------------------------------------------
def test_editar_transaccion_corrige_monto_y_categoria():
    repo = FakeRepo()
    u = _user(repo)
    r = registrar_gasto(repo, u.id, monto=32, categoria="comida")
    editado = editar_transaccion(
        repo, u.id, transaction_id=r["transaction_id"], monto=20,
        categoria="transporte", confirmado=True,
    )

    assert editado["monto"] == 20.0 and editado["categoria"] == "transporte"
    assert editado["status"] == "confirmada"
    # El total groundeado refleja la corrección (§1.2).
    assert editado["total_categoria_periodo"] == 20.0
    assert repo.sum_gastos(u.id, categoria="comida") == Decimal("0")


def test_editar_transaccion_id_ajeno_devuelve_no_encontrada():
    """Aislamiento §7.3.2: el id de otro usuario no es visible ni editable."""
    repo = FakeRepo()
    u = _user(repo)
    otro = repo.get_or_create_user("+50370000001", "Mallory")
    r = registrar_gasto(repo, otro.id, monto=99, categoria="comida")

    resultado = editar_transaccion(repo, u.id, transaction_id=r["transaction_id"], monto=1)
    assert resultado["error"] == "no_encontrada"
    # El gasto del otro usuario quedó intacto.
    assert repo.sum_gastos(otro.id, categoria="comida") == Decimal("99")


def test_editar_transaccion_sin_cambios_ni_id_invalido():
    repo = FakeRepo()
    u = _user(repo)
    r = registrar_gasto(repo, u.id, monto=10, categoria="comida")
    assert editar_transaccion(repo, u.id, transaction_id=r["transaction_id"])["error"] == "sin_cambios"
    assert editar_transaccion(repo, u.id, transaction_id="no-es-uuid", monto=5)["error"] == "no_encontrada"


# --- eliminar_transaccion -----------------------------------------------------------
def test_eliminar_transaccion_anula_y_deja_de_contar():
    repo = FakeRepo()
    u = _user(repo)
    r = registrar_gasto(repo, u.id, monto=32, categoria="comida")
    borrado = eliminar_transaccion(repo, u.id, transaction_id=r["transaction_id"], confirmado=True)

    assert borrado["status"] == "anulada"
    # No se borra la fila (audit), pero no suma en el presupuesto.
    assert len(repo.transactions) == 1
    assert repo.sum_gastos(u.id, categoria="comida") == Decimal("0")
    # Segunda anulación avisa (el error de estado gana sobre la confirmación).
    assert (
        eliminar_transaccion(repo, u.id, transaction_id=r["transaction_id"], confirmado=True)["error"]
        == "ya_anulada"
    )


def test_eliminar_transaccion_id_ajeno_devuelve_no_encontrada():
    repo = FakeRepo()
    u = _user(repo)
    otro = repo.get_or_create_user("+50370000001")
    r = registrar_ingreso(repo, otro.id, monto=100, categoria="Venta")
    assert eliminar_transaccion(repo, u.id, transaction_id=r["transaction_id"])["error"] == "no_encontrada"


# --- confirmación antes de editar / eliminar (fase 11) ------------------------------
def test_editar_sin_confirmar_pide_confirmacion_y_no_cambia_nada():
    repo = FakeRepo()
    u = _user(repo)
    r = registrar_gasto(repo, u.id, monto=32, categoria="comida")
    resp = editar_transaccion(
        repo, u.id, transaction_id=r["transaction_id"], monto=20
    )

    assert resp["status"] == "requiere_confirmacion"
    assert resp["actual"]["monto"] == 32.0
    assert resp["propuesta"]["monto"] == 20.0
    # Nada cambió: el gasto sigue en 32 hasta que el usuario confirme.
    assert repo.sum_gastos(u.id, categoria="comida") == Decimal("32")


def test_eliminar_sin_confirmar_pide_confirmacion_y_no_anula():
    repo = FakeRepo()
    u = _user(repo)
    r = registrar_gasto(repo, u.id, monto=32, categoria="comida")
    resp = eliminar_transaccion(repo, u.id, transaction_id=r["transaction_id"])

    assert resp["status"] == "requiere_confirmacion"
    assert resp["movimiento"]["monto"] == 32.0
    # Sigue contando: no se anuló nada.
    assert repo.sum_gastos(u.id, categoria="comida") == Decimal("32")


# --- duplicados ---------------------------------------------------------------------
def test_gasto_repetido_devuelve_posible_duplicado_sin_registrar():
    """Caso típico con media: anota '32 en el súper' por texto y luego manda la
    foto del mismo recibo. El segundo registro NO se guarda; Luca pregunta."""
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=32, categoria="comida", comercio="súper")
    r2 = registrar_gasto(repo, u.id, monto=32, categoria="comida", comercio="súper")

    assert r2["status"] == "posible_duplicado"
    assert r2["duplicado_de"]["monto"] == 32.0
    assert len([t for t in repo.transactions]) == 1  # no se duplicó


def test_gasto_duplicado_con_forzar_se_registra():
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=32, categoria="comida")
    r2 = registrar_gasto(repo, u.id, monto=32, categoria="comida", forzar=True)

    assert r2["status"] == "confirmada"
    assert repo.sum_gastos(u.id, categoria="comida") == Decimal("64")


def test_completar_pendiente_no_dispara_duplicado():
    """El merge del loop de confirmación no es un duplicado: mismo monto que un
    gasto previo, pero completa la transacción pendiente existente."""
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=25, categoria="comida")
    registrar_gasto(repo, u.id, categoria="transporte")  # pendiente
    r = registrar_gasto(repo, u.id, monto=25)  # completa la pendiente

    assert r["status"] == "confirmada"
    assert len(repo.transactions) == 2


def test_ingreso_repetido_devuelve_posible_duplicado():
    repo = FakeRepo()
    u = _user(repo)
    registrar_ingreso(repo, u.id, monto=450, categoria="Salario")
    r2 = registrar_ingreso(repo, u.id, monto=450, categoria="Salario")
    assert r2["status"] == "posible_duplicado"


def test_montos_distintos_no_son_duplicado():
    repo = FakeRepo()
    u = _user(repo)
    registrar_gasto(repo, u.id, monto=32, categoria="comida")
    r2 = registrar_gasto(repo, u.id, monto=15, categoria="comida")
    assert r2["status"] == "confirmada"


def test_fechas_distintas_no_son_duplicado():
    repo = FakeRepo()
    u = _user(repo)
    repo.save_transaction(
        Transaction(
            user_id=u.id,
            monto=Decimal("32"),
            fecha=date(2026, 7, 1),
            categoria="comida",
            status="confirmada",
        )
    )
    r2 = registrar_gasto(repo, u.id, monto=32, categoria="comida")  # fecha = hoy
    assert r2["status"] == "confirmada"
