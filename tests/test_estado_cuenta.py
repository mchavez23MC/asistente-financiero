"""Tests del pipeline de estado de cuenta (plan de documentos, E3):
parser tabular + normalización + dedupe + detección de columnas.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.adapters.documents.tabular import leer_tabla
from app.application.documents.estado_cuenta import procesar_estado_cuenta

_CSV_MONTO_UNICO = (
    "Fecha,Descripcion,Monto\n"
    "10/07/2026,SUPERMAXI QUITO,-32.50\n"
    "12/07/2026,PAGO NOMINA ACME,1200.00\n"
    "13/07/2026,NETFLIX,-12.99\n"
).encode()

_CSV_DEBITO_CREDITO = (
    "Fecha;Concepto;Debito;Credito\n"
    "2026-07-10;Farmacia Cruz Azul;8,50;\n"
    "2026-07-11;Transferencia recibida;;150,00\n"
).encode()


# --- tabular.py --------------------------------------------------------------
def test_lee_csv_coma():
    filas = leer_tabla(_CSV_MONTO_UNICO, "text/csv", "mov.csv")
    assert filas[0] == ["Fecha", "Descripcion", "Monto"]
    assert len(filas) == 4


def test_lee_csv_punto_y_coma():
    filas = leer_tabla(_CSV_DEBITO_CREDITO, "text/csv", "mov.csv")
    assert filas[0][0] == "Fecha" and len(filas) == 3


def test_xlsx_corrupto_devuelve_vacio():
    # Content-type de Excel pero bytes inválidos → openpyxl falla → [] (respaldo).
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert leer_tabla(b"no soy un xlsx", xlsx, "roto.xlsx") == []


# --- procesar_estado_cuenta --------------------------------------------------
def test_columna_unica_signo_decide_tipo():
    filas = leer_tabla(_CSV_MONTO_UNICO, "text/csv", "m.csv")
    r = procesar_estado_cuenta(filas, uuid4(), uuid4())
    assert r.columnas_detectadas
    assert len(r.items) == 3
    gastos = [i for i in r.items if i.tipo == "gasto"]
    ingresos = [i for i in r.items if i.tipo == "ingreso"]
    assert {float(i.monto) for i in gastos} == {32.50, 12.99}
    assert float(ingresos[0].monto) == 1200.00
    assert r.items[0].fecha == date(2026, 7, 10)
    assert r.total_gastos == Decimal("45.49")


def test_columnas_debito_credito():
    filas = leer_tabla(_CSV_DEBITO_CREDITO, "text/csv", "m.csv")
    r = procesar_estado_cuenta(filas, uuid4(), uuid4())
    assert len(r.items) == 2
    porq = {i.descripcion_raw: i for i in r.items}
    assert porq["Farmacia Cruz Azul"].tipo == "gasto"
    assert float(porq["Farmacia Cruz Azul"].monto) == 8.50
    assert porq["Transferencia recibida"].tipo == "ingreso"
    assert float(porq["Transferencia recibida"].monto) == 150.00


def test_dedupe_marca_duplicados():
    filas = leer_tabla(_CSV_MONTO_UNICO, "text/csv", "m.csv")
    # El gasto de 32.50 del 10/07 ya estaba registrado.
    yastan = {(date(2026, 7, 10), "32.50")}
    r = procesar_estado_cuenta(
        filas, uuid4(), uuid4(),
        es_duplicado=lambda f, m: (f, f"{m:.2f}") in yastan,
    )
    dups = [i for i in r.items if i.estado == "duplicado"]
    assert len(dups) == 1 and dups[0].descripcion_raw == "SUPERMAXI QUITO"
    assert r.duplicados == 1
    # El duplicado no cuenta en el total de gastos.
    assert r.total_gastos == Decimal("12.99")


def test_columnas_no_reconocidas():
    filas = leer_tabla(b"col1,col2\na,b\n", "text/csv", "x.csv")
    r = procesar_estado_cuenta(filas, uuid4(), uuid4())
    assert r.columnas_detectadas is False
    assert r.items == []


def test_montos_con_separador_de_miles():
    csv = b"Fecha,Detalle,Monto\n01/07/2026,Sueldo,\"1.500,00\"\n"
    filas = leer_tabla(csv, "text/csv", "m.csv")
    r = procesar_estado_cuenta(filas, uuid4(), uuid4())
    assert float(r.items[0].monto) == 1500.00
    assert r.items[0].tipo == "ingreso"
