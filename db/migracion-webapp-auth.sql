-- =====================================================================
-- MIGRACIÓN — rama webapp: autenticación OTP de la webapp
-- Fecha: 2026-07-13 · Agrega SOLO las tablas nuevas (auth_codes, sessions).
--
-- Es IDEMPOTENTE (create table if not exists): se puede ejecutar más de
-- una vez sin dañar nada, y NO toca las tablas existentes (users,
-- messages, transactions, budgets, tickets, alerts, categories).
--
-- CÓMO EJECUTARLA (2 minutos):
--   1. Entra a https://supabase.com/dashboard y abre el proyecto del equipo.
--   2. Menú lateral → "SQL Editor" → botón "New query".
--   3. Pega TODO este archivo y presiona "Run" (o Ctrl+Enter).
--   4. Verifica: menú "Table Editor" → deben aparecer `auth_codes` y
--      `sessions` junto a las tablas de siempre.
--
-- Qué habilita: el login de la webapp (rama webapp) — el usuario escribe su
-- teléfono, recibe un código de 6 dígitos por WhatsApp y canjea una sesión.
-- Sin estas tablas, POST /api/auth/solicitar devuelve error 500.
-- =====================================================================

-- ---------------------------------------------------------------------
-- auth_codes — códigos OTP de acceso, enviados por WhatsApp.
-- Seguridad (OWASP MFA / NIST 800-63B): solo se guarda el HASH del código
-- (sha256 con el teléfono como contexto); expira a los 5 min; es de un solo
-- uso; la app corta a los 5 intentos fallidos.
-- ---------------------------------------------------------------------
create table if not exists auth_codes (
    id          uuid primary key default gen_random_uuid(),
    telefono    text not null,
    codigo_hash text not null,
    expira_at   timestamptz not null,
    intentos    int not null default 0,
    usado       boolean not null default false,
    created_at  timestamptz not null default now()
);
create index if not exists idx_auth_codes_tel on auth_codes (telefono, created_at desc);

-- ---------------------------------------------------------------------
-- sessions — sesiones web emitidas al verificar el OTP (7 días).
-- Se guarda el hash del token (nunca el token en claro): si la tabla se
-- filtrara, los hashes no sirven para suplantar a nadie.
-- ---------------------------------------------------------------------
create table if not exists sessions (
    token_hash text primary key,
    user_id    uuid not null references users(id) on delete cascade,
    expira_at  timestamptz not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_sessions_user on sessions (user_id);

-- ---------------------------------------------------------------------
-- Verificación rápida (opcional): tras el Run, esta consulta debe devolver
-- las dos tablas nuevas.
-- ---------------------------------------------------------------------
-- select table_name from information_schema.tables
--  where table_schema = 'public' and table_name in ('auth_codes', 'sessions');
