-- =====================================================================
-- MIGRACIÓN — ingesta y revisión de documentos (plan de documentos, E1)
-- Fecha: 2026-07-17 · Agrega la infraestructura de documentos financieros:
--   - documents        (todo archivo recibido, con su original en Storage)
--   - document_items   (staging de carga masiva — se usa desde E3)
--   - counterparties   (alias de beneficiarios/comercios — desde E3)
--   - import_templates (mapeo de columnas aprendido — desde E3)
--   - review_tasks     (cola de revisión de la webapp — desde E3)
--   - transactions: columnas document_id y source (trazabilidad)
--
-- Es IDEMPOTENTE: se puede correr más de una vez sin daño y NO toca datos
-- existentes. La app degrada con gracia sin esta migración: con
-- DOCS_HABILITADO=false (el default) nada de esto se usa y el pipeline se
-- comporta exactamente como antes.
--
-- CÓMO EJECUTARLA:
--   1. https://supabase.com/dashboard → proyecto del equipo.
--   2. "SQL Editor" → "New query" → pega TODO este archivo → "Run".
--   3. Storage → "New bucket" → nombre `documentos`, PRIVADO (sin público).
--      (El código verifica el bucket al arrancar con DOCS_HABILITADO=true.)
-- =====================================================================

-- 1. documents — todo archivo recibido, con su original en Storage
create table if not exists documents (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null references users(id) on delete cascade,
    tipo_documento text not null default 'sin_clasificar' check (tipo_documento in
                     ('factura_sri','retencion','nota_credito','transferencia',
                      'planilla_servicio','estado_cuenta','rol_pagos','voucher',
                      'otro_respaldo','sin_clasificar')),
    status         text not null default 'recibido' check (status in
                     ('recibido','esperando_clasificacion','procesando',
                      'extraido','en_revision','confirmado','error','descartado')),
    storage_path   text not null,
    filename       text,
    content_type   text not null,
    size_bytes     int  not null,
    sha256         text not null,
    clave_acceso   text,                 -- SRI, 49 dígitos (factura/retención/NC)
    emisor_ruc     text,
    emisor_nombre  text,
    fecha_emision  date,
    total          numeric(12,2),
    metodo_extraccion text check (metodo_extraccion in
                     ('xml_parser','pdf_template','csv_parser','vision_ia','manual')),
    validado_sri   text check (validado_sri in
                     ('autorizado','no_autorizado','no_consultado')),
    datos_extraidos jsonb,
    error_detalle  text,
    created_at     timestamptz not null default now(),
    processed_at   timestamptz,
    unique (user_id, sha256)
);
create unique index if not exists uq_documents_clave
    on documents (user_id, clave_acceso) where clave_acceso is not null;
create index if not exists idx_documents_user
    on documents (user_id, created_at desc);
create index if not exists idx_documents_fecha
    on documents (user_id, fecha_emision desc);

-- 2. document_items — staging de carga masiva (estado de cuenta / división de
--    facturas). NO toca transactions hasta la confirmación humana (riesgo R1).
create table if not exists document_items (
    id                 uuid primary key default gen_random_uuid(),
    document_id        uuid not null references documents(id) on delete cascade,
    user_id            uuid not null references users(id) on delete cascade,
    n_linea            int  not null,
    fecha              date,
    descripcion_raw    text not null,
    monto              numeric(12,2),
    tipo               text check (tipo in ('gasto','ingreso')),
    categoria_sugerida text,
    counterparty_id    uuid,              -- FK blanda; se declara abajo
    confianza          real check (confianza between 0 and 1),
    estado             text not null default 'pendiente' check (estado in
                         ('pendiente','aceptado','rechazado','duplicado')),
    transaction_id     uuid references transactions(id) on delete set null,
    unique (document_id, n_linea)
);
create index if not exists idx_document_items_doc
    on document_items (document_id, estado);

-- 3. counterparties — beneficiarios/comercios con alias y semántica del usuario
create table if not exists counterparties (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references users(id) on delete cascade,
    patrones          text[] not null,
    alias             text not null,
    tipo_default      text check (tipo_default in ('gasto','ingreso')),
    categoria_default text,
    es_cuenta_propia  boolean not null default false,
    excluir           boolean not null default false,
    notas             text,
    created_at        timestamptz not null default now()
);
create index if not exists idx_counterparties_user on counterparties (user_id);

do $$
begin
    if not exists (
        select 1 from information_schema.table_constraints
        where constraint_name = 'document_items_counterparty_fk'
    ) then
        alter table document_items
            add constraint document_items_counterparty_fk
            foreign key (counterparty_id) references counterparties(id)
            on delete set null;
    end if;
end $$;

-- 4. import_templates — mapeo de columnas aprendido (estilo CRM)
create table if not exists import_templates (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid references users(id) on delete cascade,  -- null = seed global
    nombre            text not null,
    huella_encabezado text not null,
    mapeo             jsonb not null,
    veces_usada       int not null default 0,
    created_at        timestamptz not null default now(),
    unique (user_id, huella_encabezado)
);

-- 5. review_tasks — la cola de revisión de la webapp
create table if not exists review_tasks (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    document_id  uuid not null references documents(id) on delete cascade,
    tipo         text not null check (tipo in
                   ('carga_masiva','mapeo','dato_faltante','ajuste_nc','division_items')),
    status       text not null default 'pendiente' check (status in
                   ('pendiente','en_progreso','completada','expirada','descartada')),
    resumen      text not null,
    created_at   timestamptz not null default now(),
    completed_at timestamptz
);
create index if not exists idx_review_tasks_user
    on review_tasks (user_id, status, created_at desc);

-- 6. transactions: trazabilidad hacia el documento de respaldo.
--    Columnas nuevas nullable/con default → el código existente ni las ve.
alter table transactions add column if not exists document_id uuid
    references documents(id) on delete set null;
alter table transactions add column if not exists source text not null default 'chat';
do $$
begin
    if not exists (
        select 1 from information_schema.table_constraints
        where constraint_name = 'transactions_source_check'
    ) then
        alter table transactions
            add constraint transactions_source_check
            check (source in ('chat','documento','recurrente'));
    end if;
end $$;
