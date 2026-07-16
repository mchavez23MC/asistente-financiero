-- =====================================================================
-- MIGRACIÓN — memoria semántica (Parte A del plan de mitigación)
-- Fecha: 2026-07-16 · Agrega la infraestructura de memoria vectorial:
--   - extensión pgvector
--   - message_embeddings   (memoria conversacional recuperable por similitud)
--   - user_facts           (memoria de largo plazo: hechos estables del usuario)
--   - conversation_summaries (memoria episódica comprimida)
--   - funciones RPC de búsqueda por similitud coseno (match_*)
--
-- Es IDEMPOTENTE (create ... if not exists / create or replace): se puede
-- correr más de una vez sin daño y NO toca las tablas existentes. La app
-- degrada con gracia sin esta migración: si no hay proveedor de embeddings
-- configurado (VOYAGE_API_KEY vacío), la memoria semántica queda apagada y el
-- asistente usa solo la ventana reciente de `messages`, como antes.
--
-- CÓMO EJECUTARLA:
--   1. https://supabase.com/dashboard → proyecto del equipo.
--   2. "SQL Editor" → "New query" → pega TODO este archivo → "Run".
--   3. Verifica en "Database" → "Extensions" que `vector` esté activada, y en
--      "Table Editor" que aparezcan message_embeddings, user_facts y
--      conversation_summaries.
--
-- NOTA sobre la dimensión: los vectores son vector(1024), que corresponde al
-- modelo por defecto del adaptador (Voyage `voyage-3.5-lite`, 1024 dims). Si se
-- cambia de modelo de embeddings a otra dimensión, hay que ajustar el `1024` en
-- las tres tablas y las funciones, y re-embeber lo existente (los vectores de
-- distinta dimensión no son comparables).
-- =====================================================================

create extension if not exists vector;

-- ---------------------------------------------------------------------
-- message_embeddings — un vector por mensaje de `messages`.
-- El embedding se calcula fuera de la ruta crítica del webhook (background),
-- así que un mensaje puede existir en `messages` sin fila aquí por un instante
-- (o para siempre, si la memoria semántica está apagada). Eso es correcto: la
-- recuperación simplemente no lo encuentra hasta que su vector esté escrito.
-- on delete cascade: si el mensaje se borra (borrado en cascada del usuario),
-- su embedding se va con él.
-- ---------------------------------------------------------------------
create table if not exists message_embeddings (
    message_id uuid primary key references messages(id) on delete cascade,
    user_id    uuid not null references users(id) on delete cascade,
    embedding  vector(1024) not null,
    created_at timestamptz not null default now()
);
-- Índice HNSW para el vecino más cercano por distancia coseno. El filtro por
-- user_id (aislamiento §7.3.2) va en la cláusula WHERE de las funciones RPC.
create index if not exists idx_message_embeddings_hnsw
    on message_embeddings using hnsw (embedding vector_cosine_ops);
create index if not exists idx_message_embeddings_user
    on message_embeddings (user_id);

-- ---------------------------------------------------------------------
-- user_facts — memoria de largo plazo: hechos estables del usuario que no
-- caben en la ventana de mensajes recientes ("cobra el 30", "categoría
-- favorita: comida", "prefiere respuestas cortas"). Los extrae un job del
-- scheduler a partir de las conversaciones; se deduplican por similitud.
-- ---------------------------------------------------------------------
create table if not exists user_facts (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references users(id) on delete cascade,
    tipo              text not null default 'otro',   -- preferencia | habito | dato | otro
    contenido         text not null,
    embedding         vector(1024),                   -- null si se guardó sin embedder
    fuente_message_id uuid references messages(id) on delete set null,
    updated_at        timestamptz not null default now(),
    created_at        timestamptz not null default now()
);
create index if not exists idx_user_facts_user on user_facts (user_id, updated_at desc);
create index if not exists idx_user_facts_hnsw
    on user_facts using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------
-- conversation_summaries — memoria episódica comprimida. Cuando una "sesión"
-- queda inactiva, un job resume en 2-4 frases lo hablado y guarda el resumen
-- con su embedding. Entra al mismo pool de recuperación que los mensajes, así
-- una alusión a "lo que hablamos la semana pasada" es recuperable sin cargar
-- cientos de mensajes.
-- ---------------------------------------------------------------------
create table if not exists conversation_summaries (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    resumen    text not null,
    embedding  vector(1024),
    desde_ts   timestamptz,
    hasta_ts   timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists idx_conv_summaries_user on conversation_summaries (user_id, hasta_ts desc);
create index if not exists idx_conv_summaries_hnsw
    on conversation_summaries using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------
-- match_messages — top-k mensajes del usuario por similitud coseno con un
-- vector de consulta. SIEMPRE filtra por user_id (aislamiento §7.3.2): un
-- vector ajeno jamás sale de aquí. Devuelve similitud = 1 - distancia_coseno
-- (más alta = más parecido) y respeta un umbral mínimo.
-- ---------------------------------------------------------------------
create or replace function match_messages(
    p_user_id       uuid,
    query_embedding vector(1024),
    match_count     int   default 5,
    umbral          float default 0.75
)
returns table (
    message_id uuid,
    contenido  text,
    rol        text,
    intencion  text,
    ts         timestamptz,
    similitud  float
)
language sql stable
as $$
    select m.id,
           m.contenido,
           m.rol,
           m.intencion,
           m."timestamp",
           1 - (e.embedding <=> query_embedding) as similitud
    from message_embeddings e
    join messages m on m.id = e.message_id
    where e.user_id = p_user_id
      and 1 - (e.embedding <=> query_embedding) >= umbral
    order by e.embedding <=> query_embedding
    limit match_count;
$$;

-- ---------------------------------------------------------------------
-- match_summaries — igual que match_messages pero sobre los resúmenes.
-- ---------------------------------------------------------------------
create or replace function match_summaries(
    p_user_id       uuid,
    query_embedding vector(1024),
    match_count     int   default 3,
    umbral          float default 0.72
)
returns table (
    summary_id uuid,
    resumen    text,
    hasta_ts   timestamptz,
    similitud  float
)
language sql stable
as $$
    select s.id,
           s.resumen,
           s.hasta_ts,
           1 - (s.embedding <=> query_embedding) as similitud
    from conversation_summaries s
    where s.user_id = p_user_id
      and s.embedding is not null
      and 1 - (s.embedding <=> query_embedding) >= umbral
    order by s.embedding <=> query_embedding
    limit match_count;
$$;

-- ---------------------------------------------------------------------
-- match_user_facts — vecinos de un hecho candidato, para deduplicar en la
-- extracción (si ya existe un hecho muy parecido, se actualiza en vez de
-- insertar otro). Sin umbral aquí: el llamador decide el corte.
-- ---------------------------------------------------------------------
create or replace function match_user_facts(
    p_user_id       uuid,
    query_embedding vector(1024),
    match_count     int default 3
)
returns table (
    fact_id   uuid,
    contenido text,
    similitud float
)
language sql stable
as $$
    select f.id,
           f.contenido,
           1 - (f.embedding <=> query_embedding) as similitud
    from user_facts f
    where f.user_id = p_user_id
      and f.embedding is not null
    order by f.embedding <=> query_embedding
    limit match_count;
$$;

-- ---------------------------------------------------------------------
-- Verificación rápida (opcional):
-- ---------------------------------------------------------------------
-- select table_name from information_schema.tables
--  where table_schema = 'public'
--    and table_name in ('message_embeddings','user_facts','conversation_summaries');
