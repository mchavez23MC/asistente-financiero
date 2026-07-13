-- ---------------------------------------------------------------------
-- Migración: soporte de INGRESOS (rama nueva-funcionalidades / Fase 10)
-- ---------------------------------------------------------------------
-- Lleva una base con el esquema previo (solo gastos) al esquema nuevo.
-- Es IDEMPOTENTE: se puede re-ejecutar sin daño.
-- Cómo correr: Supabase → SQL Editor → pegar todo → Run.
-- ---------------------------------------------------------------------

-- 1. Nueva columna `tipo` en transactions. Los datos previos quedan como
--    'gasto' automáticamente (default). Esto es lo que faltaba y causaba
--    "column transactions.tipo does not exist".
alter table transactions
    add column if not exists tipo text not null default 'gasto';

-- Constraint de dominio para `tipo` (solo si aún no existe).
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'transactions_tipo_check'
    ) then
        alter table transactions
            add constraint transactions_tipo_check
            check (tipo in ('gasto','ingreso'));
    end if;
end $$;

-- 2. A lo sumo una transacción pendiente por (usuario, tipo): así un ingreso
--    a medias no pisa a un gasto a medias. Reemplaza la versión previa que
--    era solo por user_id.
drop index if exists uq_transaction_pendiente;
create unique index uq_transaction_pendiente
    on transactions (user_id, tipo)
    where status = 'pendiente_confirmacion';

-- 3. Tablas nuevas para ingresos recurrentes (idempotentes).
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

create table if not exists income_reminders (
    id            uuid primary key default gen_random_uuid(),
    recurring_id  uuid not null references recurring_incomes(id) on delete cascade,
    periodo_clave text not null,
    created_at    timestamptz not null default now(),
    unique (recurring_id, periodo_clave)
);
