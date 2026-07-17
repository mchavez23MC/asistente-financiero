"""Tests del perfil financiero verificable (E5). Regla de oro: solo lo
respaldado puntúa. Dataset sintético con valores calculados a mano.
"""

from datetime import date
from decimal import Decimal

from app.application.perfil import calcular_perfil

HOY = date(2026, 7, 15)  # meses cerrados: ene..jun 2026


def _mov(tipo, monto, mes, dia=10, respaldada=False, sri=False, comercio=None, categoria=None):
    return {
        "tipo": tipo,
        "monto": Decimal(str(monto)),
        "fecha": date(2026, mes, dia),
        "respaldada": respaldada,
        "sri": sri,
        "comercio": comercio,
        "categoria": categoria,
    }


def test_sin_datos_estado_construyendo():
    p = calcular_perfil([], hoy=HOY)
    assert p["estado"] == "construyendo"
    assert p["confiabilidad_del_dato"]["indice_verificacion"] == 0.0
    assert p["usuario"]["meses_activo"] == 0


def test_ingreso_regular_respaldado_sube_el_indice():
    # 6 meses (ene..jun) con un ingreso respaldado de 500 cada uno.
    movs = [_mov("ingreso", 500, mes, respaldada=True, comercio="Ferretería") for mes in range(1, 7)]
    p = calcular_perfil(movs, hoy=HOY)
    assert p["estado"] == "activo"
    assert p["ingresos"]["promedio_mensual"] == 500.0
    assert p["ingresos"]["regularidad"] == 1.0  # 6 de 6 meses
    assert p["ingresos"]["fuentes"][0]["alias"] == "Ferretería"
    assert p["ingresos"]["fuentes"][0]["meses_consecutivos"] == 6
    # índice alto: regularidad 1.0, respaldado 1.0, antigüedad 6/6, evidencia 0.7
    # = 0.40 + 0.25 + 0.20 + 0.15*0.7 = 0.955 → redondea a 0.96
    assert p["confiabilidad_del_dato"]["indice_verificacion"] == 0.96


def test_todo_declarado_indice_bajo():
    # Mismos ingresos pero SIN respaldo → no puntúan (regla de oro).
    movs = [_mov("ingreso", 500, mes, respaldada=False) for mes in range(1, 7)]
    p = calcular_perfil(movs, hoy=HOY)
    assert p["ingresos"]["promedio_mensual"] == 0.0  # nada respaldado
    assert p["ingresos"]["regularidad"] == 0.0
    # solo antigüedad puntúa: 0.20 * 1.0 = 0.20
    assert p["confiabilidad_del_dato"]["indice_verificacion"] == 0.2


def test_sri_pesa_mas_que_foto():
    con_sri = [_mov("ingreso", 500, mes, respaldada=True, sri=True) for mes in range(1, 7)]
    con_foto = [_mov("ingreso", 500, mes, respaldada=True, sri=False) for mes in range(1, 7)]
    p_sri = calcular_perfil(con_sri, hoy=HOY)
    p_foto = calcular_perfil(con_foto, hoy=HOY)
    idx_sri = p_sri["confiabilidad_del_dato"]["indice_verificacion"]
    idx_foto = p_foto["confiabilidad_del_dato"]["indice_verificacion"]
    assert idx_sri > idx_foto
    assert p_sri["confiabilidad_del_dato"]["con_validacion_sri"] == 6


def test_gastos_respaldado_pct():
    movs = [
        _mov("gasto", 100, 3, respaldada=True, categoria="comida"),
        _mov("gasto", 100, 3, respaldada=False, categoria="ropa"),
        _mov("gasto", 200, 4, respaldada=True, categoria="comida"),
    ]
    p = calcular_perfil(movs, hoy=HOY)
    # 300 respaldado de 400 total = 0.75
    assert p["gastos"]["respaldado_pct"] == 0.75
    assert "comida" in p["gastos"]["categorias_top"]


def test_capacidad_flujo_neto_y_ratio():
    movs = [
        _mov("ingreso", 1000, 5, respaldada=True),
        _mov("gasto", 600, 5, respaldada=True),
    ]
    p = calcular_perfil(movs, hoy=HOY)
    assert p["capacidad"]["ratio_gasto_ingreso"] == 0.6
    assert p["capacidad"]["flujo_neto_mensual"] == 400.0


def test_mes_en_curso_no_cuenta():
    # Un ingreso en julio (mes en curso) no entra en los promedios de 6m cerrados.
    movs = [_mov("ingreso", 999, 7, dia=1, respaldada=True)]
    p = calcular_perfil(movs, hoy=HOY)
    assert p["ingresos"]["promedio_mensual"] == 0.0
    # pero sí cuenta como mes activo (antigüedad)
    assert p["usuario"]["meses_activo"] == 1
