"""Contrato de las tools del agente (con T1) — Fase 1, CONGELADO.

Firma de las tools que Claude puede invocar. Estas definiciones se pasan tal
cual al SDK de Anthropic en fase 4 (`tools=TOOLS`). El *system prompt* sigue
iterándose después, pero estos nombres/parámetros/retornos NO cambian sin
renegociación explícita (§8.2).

Renegociación (fase 10 — correcciones y media): se AÑADEN consultar_movimientos,
editar_transaccion y eliminar_transaccion, y el parámetro `forzar` (detección de
duplicados) a registrar_gasto/registrar_ingreso. Las firmas previas no cambian.

Renegociación (fase 11 — confirmación explícita): se AÑADE el parámetro `confirmado`
a editar_transaccion, eliminar_transaccion y crear_ticket. Sin `confirmado=true`
estas tools NO ejecutan: devuelven 'requiere_confirmacion' para que el agente
pregunte al usuario antes de corregir, anular o escalar.

Renegociación (fase 11 — presupuestos por chat): se AÑADE configurar_presupuesto,
que reutiliza el mismo upsert (`save_budget`) del endpoint de la webapp para dar
paridad chat ↔ web. Las firmas previas no cambian.

INVARIANTE DE SEGURIDAD (§7.3.2): el `user_id` NUNCA es parámetro de ninguna
tool. Lo resuelve el `Repository` desde el teléfono del webhook. Aunque el
modelo "quisiera" pedir datos de otro usuario, no tiene cómo expresarlo — el
aislamiento vive en el adaptador de persistencia, no en el prompt.

Cada schema sigue el formato de tool de Anthropic:
  {"name": str, "description": str, "input_schema": {json-schema de params}}
"""

from __future__ import annotations

# Parámetro de confirmación compartido (fase 11): las acciones que corrigen,
# anulan o escalan piden confirmación explícita del usuario antes de ejecutar.
_CONFIRMADO_SCHEMA = {
    "type": "boolean",
    "description": (
        "true SOLO después de que el usuario confirmó explícitamente esta acción "
        "en su último mensaje. En la PRIMERA llamada NO lo pongas: la tool "
        "devolverá 'requiere_confirmacion' y debes preguntarle antes de ejecutar."
    ),
}

# --- H1: registrar un gasto -------------------------------------------------
REGISTRAR_GASTO = {
    "name": "registrar_gasto",
    "description": (
        "Registra un gasto del usuario. Si falta información obligatoria (monto), "
        "la transacción queda en estado 'pendiente_confirmacion' y debes pedir el "
        "dato faltante en tu respuesta. No inventes montos ni fechas."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "monto": {
                "type": "number",
                "description": "Monto del gasto, positivo. Omitir si el usuario no lo dio.",
            },
            "fecha": {
                "type": "string",
                "format": "date",
                "description": "Fecha del gasto (YYYY-MM-DD). Si no se especifica, usar hoy.",
            },
            "categoria": {
                "type": "string",
                "description": "Categoría del gasto (ej. 'comida', 'transporte').",
            },
            "comercio": {
                "type": "string",
                "description": "Nombre del comercio o descripción corta.",
            },
            "forzar": {
                "type": "boolean",
                "description": (
                    "true SOLO si la tool ya devolvió 'posible_duplicado' y el "
                    "usuario confirmó que es un movimiento distinto. Nunca en la "
                    "primera llamada."
                ),
            },
        },
        "required": [],  # nada es obligatorio a nivel schema: el status maneja lo incompleto
    },
    # Retorno (documentado, no parte del schema de Anthropic):
    #   {"transaction_id": str, "status": "confirmada"|"pendiente_confirmacion",
    #    "faltantes": [str]}  -> lista de campos que aún faltan para confirmar.
    #   Si detecta un posible duplicado (mismo monto/fecha/tipo ya registrado):
    #   {"status": "posible_duplicado", "duplicado_de": {...}} SIN registrar nada;
    #   pregunta al usuario y reintenta con forzar=true si él confirma.
}

# --- H1: registrar un ingreso (espejo de registrar_gasto) -------------------
CATEGORIAS_INGRESO = [
    "Salario",
    "Freelance/Independiente",
    "Bono o comisión",
    "Reembolso",
    "Regalo",
    "Venta",
    "Otro ingreso",
]

REGISTRAR_INGRESO = {
    "name": "registrar_ingreso",
    "description": (
        "Registra un ingreso del usuario (sueldo, freelance, venta, regalo, etc.). "
        "Si falta información obligatoria (monto), el ingreso queda en estado "
        "'pendiente_confirmacion' y debes pedir el dato faltante. No inventes "
        "montos ni fuentes. Usa esta tool, NO registrar_gasto, para plata que ENTRA."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "monto": {
                "type": "number",
                "description": "Monto del ingreso, positivo. Omitir si el usuario no lo dio.",
            },
            "fecha": {
                "type": "string",
                "format": "date",
                "description": "Fecha del ingreso (YYYY-MM-DD). Si no se especifica, usar hoy.",
            },
            "categoria": {
                "type": "string",
                "enum": CATEGORIAS_INGRESO,
                "description": "Categoría del ingreso. Elige la más cercana del enum.",
            },
            "fuente": {
                "type": "string",
                "description": "De dónde viene (empresa, cliente, 'venta de la bici'…).",
            },
            "forzar": {
                "type": "boolean",
                "description": (
                    "true SOLO si la tool ya devolvió 'posible_duplicado' y el "
                    "usuario confirmó que es un movimiento distinto. Nunca en la "
                    "primera llamada."
                ),
            },
        },
        "required": [],  # nada obligatorio a nivel schema: el status maneja lo incompleto
    },
    # Retorno documentado:
    #   {"transaction_id": str, "status": "confirmada"|"pendiente_confirmacion",
    #    "faltantes": [str], "total_categoria_periodo": number}
    #   Puede devolver {"status": "posible_duplicado", "duplicado_de": {...}}
    #   igual que registrar_gasto.
}

# --- H1: configurar un ingreso fijo mensual (sueldo base recurrente) ---------
CONFIGURAR_INGRESO_RECURRENTE = {
    "name": "configurar_ingreso_recurrente",
    "description": (
        "Configura, actualiza o desactiva un ingreso fijo mensual del usuario "
        "(ej. su sueldo base). NO registra el ingreso: cada mes el sistema le "
        "recordará al usuario para que confirme y recién ahí se registra. Úsala "
        "cuando el usuario describa un ingreso que se repite todos los meses "
        "('mi sueldo es 450 y me pagan el 30'). El usuario puede tener varios."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["crear", "actualizar", "desactivar"],
                "description": "Qué hacer con la recurrencia. Por defecto 'crear'.",
            },
            "monto": {
                "type": "number",
                "description": "Monto fijo del ingreso mensual, positivo.",
            },
            "categoria": {
                "type": "string",
                "enum": CATEGORIAS_INGRESO,
                "description": "Categoría del ingreso recurrente. Por defecto 'Salario'.",
            },
            "fuente": {
                "type": "string",
                "description": "De dónde viene (empresa, arriendo, etc.).",
            },
            "dia_del_mes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 28,
                "description": (
                    "Día del mes en que suele llegar (1-28). Si el usuario dice un "
                    "día > 28, usa 28 y avísale con calidez que lo ajustaste."
                ),
            },
        },
        "required": ["accion"],
    },
    # Retorno documentado:
    #   {"recurring_id": str, "activo": bool, "monto": number, "categoria": str,
    #    "dia_del_mes": int}
}

# --- H2: consultar presupuesto / resumen de gastos --------------------------
CONSULTAR_PRESUPUESTO = {
    "name": "consultar_presupuesto",
    "description": (
        "Consulta el estado del presupuesto y el total gastado del usuario en un "
        "periodo. EL SISTEMA CALCULA LOS NÚMEROS; tú solo los explicas — nunca "
        "sumes o estimes totales por tu cuenta (H2 es grounded)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "periodo": {
                "type": "string",
                "enum": ["semanal", "mensual", "anual"],
                "description": "Periodo a consultar. Por defecto 'mensual'.",
            },
            "categoria": {
                "type": "string",
                "description": "Categoría específica a consultar, u omitir para el total.",
            },
        },
        "required": [],
    },
    # Retorno documentado:
    #   {"periodo": str, "categoria": str|null, "limite": number|null,
    #    "gastado": number, "restante": number|null, "porcentaje": number|null}
}

# --- H2: crear/actualizar un presupuesto por chat ----------------------------
CONFIGURAR_PRESUPUESTO = {
    "name": "configurar_presupuesto",
    "description": (
        "Crea o actualiza el presupuesto del usuario para una categoría y "
        "periodo ('ponme un límite de 100 en comida al mes'). Si ya existía un "
        "límite para esa categoría/periodo lo reemplaza y el retorno trae el "
        "monto anterior — menciónale el cambio al usuario. El sistema le "
        "avisará solo cuando se acerque al límite (umbral de alerta). No la "
        "uses para registrar movimientos ni para consultar cómo va: para eso "
        "están registrar_gasto y consultar_presupuesto."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "categoria": {
                "type": "string",
                "description": "Categoría del presupuesto (ej. 'comida', 'transporte').",
            },
            "monto_limite": {
                "type": "number",
                "description": "Límite del presupuesto, positivo. Omitir si el usuario no lo dio.",
            },
            "periodo": {
                "type": "string",
                "enum": ["semanal", "mensual", "anual"],
                "description": "Periodo del límite. Por defecto 'mensual'.",
            },
            "umbral_alerta": {
                "type": "number",
                "description": (
                    "Fracción del límite [0..1] que dispara la alerta proactiva "
                    "(0.8 = avisar al 80%). Solo si el usuario lo pide; por "
                    "defecto 0.8, y al actualizar se conserva el que tenía."
                ),
            },
        },
        "required": ["categoria"],
    },
    # Retorno documentado:
    #   {"budget_id": str, "categoria": str, "monto_limite": number,
    #    "periodo": str, "umbral_alerta": number,
    #    "anterior_monto_limite": number|null}  (número previo si fue actualización)
    #   o {"error": "faltan_datos", "faltantes": [str]} si falta categoría o monto.
}

# --- H3: responder soporte desde el corpus (grounded) -----------------------
RESPONDER_SOPORTE = {
    "name": "responder_soporte",
    "description": (
        "Responde una pregunta de soporte USANDO SOLO el corpus de conocimiento "
        "provisto en el contexto. Si la respuesta no está en el corpus, NO "
        "inventes: llama a 'crear_ticket' con motivo 'fuera_de_corpus' (§4)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pregunta": {
                "type": "string",
                "description": "La pregunta de soporte del usuario, reformulada si hace falta.",
            },
        },
        "required": ["pregunta"],
    },
    # Retorno documentado:
    #   {"respuesta": str, "encontrado_en_corpus": bool, "cita": str|null}
}

# --- H1: consultar el historial de movimientos ------------------------------
CONSULTAR_MOVIMIENTOS = {
    "name": "consultar_movimientos",
    "description": (
        "Lista los últimos movimientos registrados del usuario (gastos y/o "
        "ingresos), más recientes primero. Úsala para '¿qué anoté ayer?', "
        "'muéstrame mis últimos 5 gastos', y SIEMPRE antes de editar o eliminar "
        "una transacción, para obtener su transaction_id. Los números vienen del "
        "sistema; no los recalcules."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limite": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Cuántos movimientos listar. Por defecto 5.",
            },
            "tipo": {
                "type": "string",
                "enum": ["gasto", "ingreso", "todos"],
                "description": "Filtrar por tipo. Por defecto 'todos'.",
            },
            "categoria": {
                "type": "string",
                "description": "Categoría específica, u omitir para todas.",
            },
        },
        "required": [],
    },
    # Retorno documentado:
    #   {"movimientos": [{"transaction_id": str, "tipo": str, "monto": number,
    #     "fecha": str, "categoria": str|null, "comercio_o_fuente": str|null}],
    #    "cuantos": int}
}

# --- H1: corregir una transacción ya registrada ------------------------------
EDITAR_TRANSACCION = {
    "name": "editar_transaccion",
    "description": (
        "Corrige una transacción ya registrada (monto, fecha, categoría, "
        "comercio/fuente o tipo). Úsala cuando el usuario corrija algo que ya "
        "quedó registrado: 'no, eran 20 no 32', 'cámbialo a Transporte'. "
        "Necesitas el transaction_id: tómalo del retorno de registrar_gasto/"
        "registrar_ingreso en esta conversación, o búscalo con "
        "consultar_movimientos. Solo cambia los campos que envíes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "transaction_id": {
                "type": "string",
                "description": "Id de la transacción a corregir.",
            },
            "monto": {"type": "number", "description": "Nuevo monto, positivo."},
            "fecha": {
                "type": "string",
                "format": "date",
                "description": "Nueva fecha (YYYY-MM-DD).",
            },
            "categoria": {"type": "string", "description": "Nueva categoría."},
            "comercio": {
                "type": "string",
                "description": "Nuevo comercio (gasto) o fuente (ingreso).",
            },
            "tipo": {
                "type": "string",
                "enum": ["gasto", "ingreso"],
                "description": "Solo si el usuario aclara que era ingreso y no gasto (o al revés).",
            },
            "confirmado": _CONFIRMADO_SCHEMA,
        },
        "required": ["transaction_id"],
    },
    # Retorno documentado:
    #   {"transaction_id": str, "status": str, "monto": number, "fecha": str,
    #    "categoria": str|null, "comercio": str|null, "tipo": str,
    #    "total_categoria_periodo": number|null}
    #   o {"error": "no_encontrada"} si el id no existe para este usuario.
    #   Sin confirmado=true: {"status": "requiere_confirmacion", "actual": {...},
    #   "propuesta": {...}} SIN aplicar el cambio; pregunta y repite con confirmado=true.
}

# --- H1: eliminar (anular) una transacción -----------------------------------
ELIMINAR_TRANSACCION = {
    "name": "eliminar_transaccion",
    "description": (
        "Anula una transacción registrada ('borra el último gasto', 'ese no va'). "
        "No borra la fila: la marca 'anulada' y deja de contar en presupuestos y "
        "totales (queda rastro de auditoría). Necesitas el transaction_id (del "
        "retorno de otra tool o de consultar_movimientos). Antes de anular, "
        "confirma con el usuario CUÁL movimiento es, salvo que sea inequívoco."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "transaction_id": {
                "type": "string",
                "description": "Id de la transacción a anular.",
            },
            "confirmado": _CONFIRMADO_SCHEMA,
        },
        "required": ["transaction_id"],
    },
    # Retorno documentado:
    #   {"transaction_id": str, "status": "anulada", "monto": number|null,
    #    "categoria": str|null}  o  {"error": "no_encontrada"}.
    #   Sin confirmado=true: {"status": "requiere_confirmacion", "movimiento": {...}}
    #   SIN anular nada; confirma con el usuario y repite con confirmado=true.
}

# --- transversal: escalar a humano -----------------------------------------
CREAR_TICKET = {
    "name": "crear_ticket",
    "description": (
        "Escala la conversación a un agente humano creando un ticket. Úsalo cuando "
        "no puedas resolver desde el corpus (H3), o cuando el usuario lo pida "
        "explícitamente. Los casos sensibles/regulatorios ya los atrapa el "
        "guardrail antes de llegar a ti (§7.3); esto es el escape restante."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "enum": [
                    "reclamo",
                    "regulatorio",
                    "consejo_inversion",
                    "fraude",
                    "fuera_de_corpus",
                    "otro",
                ],
                "description": "Motivo de la escalación.",
            },
            "prioridad": {
                "type": "string",
                "enum": ["baja", "media", "alta"],
                "description": "Prioridad estimada. Por defecto 'media'.",
            },
            "contexto": {
                "type": "string",
                "description": "Resumen para el agente humano: qué pidió el usuario y por qué se escala.",
            },
            "confirmado": _CONFIRMADO_SCHEMA,
        },
        "required": ["motivo", "contexto"],
    },
    # Retorno documentado:
    #   {"ticket_id": str, "estado": "abierto"}
    #   Para motivos de "pedir ayuda" (fuera_de_corpus, otro), sin confirmado=true:
    #   {"status": "requiere_confirmacion", ...} SIN crear el ticket; pregunta al
    #   usuario si quiere que escales y repite con confirmado=true. Los motivos
    #   sensibles (reclamo, regulatorio, consejo_inversion, fraude) escalan directo.
}

# --- Documentos: consultar los respaldos guardados (plan de documentos) ------
CONSULTAR_DOCUMENTOS = {
    "name": "consultar_documentos",
    "description": (
        "Lista los documentos/respaldos que el usuario te ha enviado (facturas, "
        "comprobantes, planillas, estados de cuenta), más recientes primero. "
        "Úsala cuando pregunte por sus respaldos ('¿qué facturas tengo de "
        "junio?', '¿guardaste el comprobante de la luz?'). SIEMPRE consúltala "
        "antes de decir que no tienes algo. El sistema lista; tú solo lo "
        "explicas — no inventes documentos."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "desde": {
                "type": "string",
                "description": "Fecha inicial YYYY-MM-DD (por fecha de recepción). Opcional.",
            },
            "hasta": {
                "type": "string",
                "description": "Fecha final YYYY-MM-DD. Opcional.",
            },
            "tipo": {
                "type": "string",
                "enum": [
                    "factura_sri",
                    "transferencia",
                    "planilla_servicio",
                    "estado_cuenta",
                    "rol_pagos",
                    "voucher",
                    "otro_respaldo",
                ],
                "description": "Filtrar por tipo de documento. Opcional.",
            },
            "limite": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Cuántos listar. Por defecto 10.",
            },
        },
        "required": [],
    },
    # Retorno documentado:
    #   {"documentos": [{"tipo": str, "emisor": str|null, "fecha": str|null,
    #     "total": number|null, "archivo": str|null}], "cuantos": int}
}

#: Set completo de tools del agente principal (H1 + H2 + H3 como opción C, §1).
TOOLS: list[dict] = [
    REGISTRAR_GASTO,
    REGISTRAR_INGRESO,
    CONFIGURAR_INGRESO_RECURRENTE,
    CONSULTAR_MOVIMIENTOS,
    EDITAR_TRANSACCION,
    ELIMINAR_TRANSACCION,
    CONSULTAR_PRESUPUESTO,
    CONFIGURAR_PRESUPUESTO,
    RESPONDER_SOPORTE,
    CREAR_TICKET,
    CONSULTAR_DOCUMENTOS,
]

#: Nombres válidos, para validación en el dispatcher (fase 2/4).
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)
