"""Contrato de las tools del agente (con T1) — Fase 1, CONGELADO.

Firma de las 4 tools que Claude puede invocar. Estas definiciones se pasan tal
cual al SDK de Anthropic en fase 4 (`tools=TOOLS`). El *system prompt* sigue
iterándose después, pero estos nombres/parámetros/retornos NO cambian sin
renegociación explícita (§8.2).

INVARIANTE DE SEGURIDAD (§7.3.2): el `user_id` NUNCA es parámetro de ninguna
tool. Lo resuelve el `Repository` desde el teléfono del webhook. Aunque el
modelo "quisiera" pedir datos de otro usuario, no tiene cómo expresarlo — el
aislamiento vive en el adaptador de persistencia, no en el prompt.

Cada schema sigue el formato de tool de Anthropic:
  {"name": str, "description": str, "input_schema": {json-schema de params}}
"""

from __future__ import annotations

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
        },
        "required": [],  # nada es obligatorio a nivel schema: el status maneja lo incompleto
    },
    # Retorno (documentado, no parte del schema de Anthropic):
    #   {"transaction_id": str, "status": "confirmada"|"pendiente_confirmacion",
    #    "faltantes": [str]}  -> lista de campos que aún faltan para confirmar.
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
        },
        "required": ["motivo", "contexto"],
    },
    # Retorno documentado:
    #   {"ticket_id": str, "estado": "abierto"}
}

#: Set completo de tools del agente principal (H1 + H2 + H3 como opción C, §1).
TOOLS: list[dict] = [
    REGISTRAR_GASTO,
    CONSULTAR_PRESUPUESTO,
    RESPONDER_SOPORTE,
    CREAR_TICKET,
]

#: Nombres válidos, para validación en el dispatcher (fase 2/4).
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)
