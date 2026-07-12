-- =====================================================================
-- E5 — Schema de Supabase (Postgres) — Fase 1, CONGELADO
-- Espeja app/domain/models.py 1:1. Ejecutar en el SQL editor de Supabase.
--
-- Convenciones:
--   - PK uuid con gen_random_uuid() (extensión pgcrypto, ya activa en Supabase).
--   - Dinero en numeric(12,2), NUNCA float (coherente con Decimal en Pydantic).
--   - timestamptz siempre (la app trabaja en UTC).
--   - El aislamiento por usuario se aplica en el Repository (§7.3.2); estas FKs
--     lo respaldan a nivel de integridad referencial.
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- users — identidad por teléfono E.164 (§3.1)
-- ---------------------------------------------------------------------
create table if not exists users (
    id                uuid primary key default gen_random_uuid(),
    telefono          text not null unique,
    nombre            text,
    consentimiento_at timestamptz,               -- paso (0) del orquestador (§7.2)
    created_at        timestamptz not null default now(),
    constraint telefono_e164 check (telefono ~ '^\+[1-9][0-9]{7,14}$')
);

-- ---------------------------------------------------------------------
-- categories — catálogo de categorías de gasto (schema de T2)
-- ---------------------------------------------------------------------
create table if not exists categories (
    id          uuid primary key default gen_random_uuid(),
    nombre      text not null unique,
    descripcion text
);

-- ---------------------------------------------------------------------
-- messages — memoria conversacional Y audit trail (§7.4)
-- ---------------------------------------------------------------------
create table if not exists messages (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    rol          text not null check (rol in ('user', 'assistant', 'system')),
    contenido    text not null,
    intencion    text check (intencion in
                    ('gasto','ingreso','presupuesto','soporte','sensible','consentimiento','otro')),
    tool_llamada text,
    timestamp    timestamptz not null default now()
);
create index if not exists idx_messages_user_ts on messages (user_id, timestamp desc);

-- ---------------------------------------------------------------------
-- transactions — movimientos (H1): gastos e ingresos, con status de
-- confirmación (§3.1). `tipo` default 'gasto' deja correctos los datos previos
-- a los ingresos sin migración. Para un ingreso, `comercio` guarda la fuente.
-- ---------------------------------------------------------------------
create table if not exists transactions (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    tipo       text not null default 'gasto' check (tipo in ('gasto','ingreso')),
    monto      numeric(12,2) check (monto is null or monto > 0),
    fecha      date,
    categoria  text,
    comercio   text,
    status     text not null default 'pendiente_confirmacion'
                 check (status in ('confirmada','pendiente_confirmacion','anulada')),
    created_at timestamptz not null default now()
);
create index if not exists idx_transactions_user on transactions (user_id, fecha desc);
-- A lo sumo una transacción pendiente por usuario Y tipo (el loop de confirmación
-- de H1): así un ingreso a medias no pisa a un gasto a medias, ni viceversa.
-- (Se dropea primero por si existe la versión previa solo-por-user_id con el
-- mismo nombre — `create ... if not exists` no la redefiniría.)
drop index if exists uq_transaction_pendiente;
create unique index uq_transaction_pendiente
    on transactions (user_id, tipo)
    where status = 'pendiente_confirmacion';

-- ---------------------------------------------------------------------
-- budgets — presupuesto por categoría/periodo (H2)
-- ---------------------------------------------------------------------
create table if not exists budgets (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references users(id) on delete cascade,
    categoria     text not null,
    monto_limite  numeric(12,2) not null check (monto_limite > 0),
    periodo       text not null default 'mensual'
                    check (periodo in ('semanal','mensual','anual')),
    umbral_alerta real not null default 0.8 check (umbral_alerta between 0 and 1),
    created_at    timestamptz not null default now(),
    unique (user_id, categoria, periodo)
);

-- ---------------------------------------------------------------------
-- tickets — escalación a humano (§7.3 / §4), visible en el panel (fase 6)
-- ---------------------------------------------------------------------
create table if not exists tickets (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references users(id) on delete cascade,
    motivo            text not null check (motivo in
                        ('reclamo','regulatorio','consejo_inversion','fraude',
                         'fuera_de_corpus','guardrail_fail_closed','otro')),
    prioridad         text not null default 'media'
                        check (prioridad in ('baja','media','alta')),
    estado            text not null default 'abierto'
                        check (estado in ('abierto','en_proceso','resuelto','cerrado')),
    contexto          text not null,
    mensaje_origen_id uuid references messages(id) on delete set null,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    resuelto_at       timestamptz
);
create index if not exists idx_tickets_estado on tickets (estado, prioridad, created_at desc);

-- ---------------------------------------------------------------------
-- alerts — idempotencia del scheduler proactivo (fase 7, §7.6)
-- Una fila por (presupuesto, periodo) ya notificado, para no alertar dos veces
-- el mismo cruce de umbral.
-- ---------------------------------------------------------------------
create table if not exists alerts (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references users(id) on delete cascade,
    budget_id     uuid not null references budgets(id) on delete cascade,
    periodo_clave text not null,               -- ej. 'mensual:2026-07'
    created_at    timestamptz not null default now(),
    unique (budget_id, periodo_clave)
);

-- ---------------------------------------------------------------------
-- recurring_incomes — ingreso fijo mensual (sueldo base, etc.)
-- El scheduler recuerda; el usuario confirma por chat (decisión D3 del plan).
-- ---------------------------------------------------------------------
create table if not exists recurring_incomes (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(id) on delete cascade,
    monto       numeric(12,2) not null check (monto > 0),
    categoria   text not null default 'Salario',
    fuente      text,
    dia_del_mes int  not null check (dia_del_mes between 1 and 28),
    activo      boolean not null default true,
    created_at  timestamptz not null default now()
);
create index if not exists idx_recurring_user on recurring_incomes (user_id, activo);

-- ---------------------------------------------------------------------
-- income_reminders — idempotencia del recordatorio de ingreso recurrente.
-- Una fila por (recurrencia, periodo) ya recordado (mismo patrón que alerts).
-- ---------------------------------------------------------------------
create table if not exists income_reminders (
    id            uuid primary key default gen_random_uuid(),
    recurring_id  uuid not null references recurring_incomes(id) on delete cascade,
    periodo_clave text not null,               -- ej. 'mensual:2026-07'
    created_at    timestamptz not null default now(),
    unique (recurring_id, periodo_clave)
);
