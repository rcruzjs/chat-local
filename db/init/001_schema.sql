-- Chat LLM Local — esquema da Fase 1 (14 tabelas)
-- Executado automaticamente na primeira subida do container "db".

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------- Usuários e acesso ----------
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nome          text NOT NULL,
    email         text NOT NULL UNIQUE,
    senha_hash    text NOT NULL,
    papel         text NOT NULL DEFAULT 'usuario' CHECK (papel IN ('admin', 'curador', 'usuario')),
    nivel_acesso  text NOT NULL DEFAULT 'local' CHECK (nivel_acesso IN ('local', 'departamental', 'empresarial')),
    ativo         boolean NOT NULL DEFAULT true,
    criada_em     timestamptz NOT NULL DEFAULT now()
);

-- ---------- Registro de modelos ----------
CREATE TABLE llm_models (
    id            serial PRIMARY KEY,
    nome          text NOT NULL UNIQUE,          -- nome no LiteLLM
    papel         text NOT NULL CHECK (papel IN ('chat', 'utilitario', 'visao', 'embedding', 'reranker', 'decisao')),
    endpoint      text NOT NULL,
    local         boolean NOT NULL DEFAULT true, -- false = nuvem
    contexto_max  integer NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    ativo         boolean NOT NULL DEFAULT true,
    criada_em     timestamptz NOT NULL DEFAULT now()
);

-- ---------- Coleções de documentos ----------
CREATE TABLE collections (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nome          text NOT NULL UNIQUE,
    descricao     text,
    nivel_acesso  text NOT NULL DEFAULT 'local' CHECK (nivel_acesso IN ('local', 'departamental', 'empresarial')),
    sensivel      boolean NOT NULL DEFAULT true,   -- true = nunca sai da máquina
    criada_em     timestamptz NOT NULL DEFAULT now(),
    excluida_em   timestamptz
);

CREATE TABLE collection_access (
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    colecao_id    uuid NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    permissao     text NOT NULL CHECK (permissao IN ('ler', 'enviar', 'administrar')),
    criada_em     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, colecao_id)
);

-- ---------- Conversas e log ----------
CREATE TABLE conversations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id),
    titulo        text,
    modelo_id     integer REFERENCES llm_models(id),
    colecao_id    uuid REFERENCES collections(id),
    summary       text,                          -- memória curta (resumo cumulativo)
    criada_em     timestamptz NOT NULL DEFAULT now(),
    excluida_em   timestamptz
);
CREATE INDEX ix_conversations_user ON conversations (user_id, criada_em DESC);

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    papel           text NOT NULL CHECK (papel IN ('system', 'user', 'assistant', 'tool')),
    conteudo        text NOT NULL,
    modelo_id       integer REFERENCES llm_models(id),
    executado_em    text,                        -- 'local' ou nome do provedor de nuvem
    params          jsonb,
    tokens_in       integer,
    tokens_out      integer,
    latencia_ms     integer,
    custo_usd       numeric(12, 6),
    criada_em       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_messages_conversation ON messages (conversation_id, criada_em);

-- ---------- Documentos e RAG ----------
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    colecao_id      uuid NOT NULL REFERENCES collections(id),
    nome            text NOT NULL,
    hash_sha256     char(64) NOT NULL,
    versao          integer NOT NULL DEFAULT 1,
    tipo_doc        text,                        -- extrato_bancario, nota_fiscal, ..., generico
    mime            text,
    status          text NOT NULL DEFAULT 'recebido'
                    CHECK (status IN ('recebido', 'processando', 'indexado', 'revisao', 'erro', 'substituido')),
    erro            text,
    caminho_arquivo text NOT NULL,
    enviado_por     uuid REFERENCES users(id),
    criada_em       timestamptz NOT NULL DEFAULT now(),
    excluida_em     timestamptz,
    UNIQUE (colecao_id, hash_sha256)
);

CREATE TABLE chunks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    colecao_id    uuid NOT NULL REFERENCES collections(id),
    ordem         integer NOT NULL,
    pagina        integer,
    secao         text,
    texto         text NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto)) STORED,
    embedding     vector(1024),
    metadados     jsonb NOT NULL DEFAULT '{}'::jsonb,
    criada_em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX ix_chunks_colecao ON chunks (colecao_id);

-- ---------- O que entrou em cada resposta ----------
CREATE TABLE message_context (
    message_id    uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    fonte         text NOT NULL CHECK (fonte IN ('chunk', 'memoria')),
    fonte_id      uuid NOT NULL,
    score         real,
    posicao       integer,
    PRIMARY KEY (message_id, fonte, fonte_id)
);

CREATE TABLE feedback (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id    uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id),
    nota          smallint NOT NULL CHECK (nota IN (-1, 1)),
    comentario    text,
    criada_em     timestamptz NOT NULL DEFAULT now()
);

-- ---------- Memória longa ----------
CREATE TABLE memory_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fato              text NOT NULL,
    tipo              text NOT NULL DEFAULT 'contexto'
                      CHECK (tipo IN ('preferencia', 'contexto', 'decisao', 'projeto')),
    embedding         vector(1024),
    origem_message_id uuid REFERENCES messages(id) ON DELETE SET NULL,
    ultimo_uso        timestamptz,
    ativo             boolean NOT NULL DEFAULT true,
    criada_em         timestamptz NOT NULL DEFAULT now(),
    atualizada_em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_memory_user ON memory_items (user_id) WHERE ativo;
CREATE INDEX ix_memory_embedding ON memory_items USING hnsw (embedding vector_cosine_ops);

-- ---------- OCR ----------
CREATE TABLE ocr_doc_types (
    id            serial PRIMARY KEY,
    codigo        text NOT NULL UNIQUE,
    nome          text NOT NULL,
    esquema_json  jsonb NOT NULL,
    exemplos      jsonb NOT NULL DEFAULT '[]'::jsonb,   -- few-shot anotados
    regras        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- validações
    ativo         boolean NOT NULL DEFAULT true
);

CREATE TABLE ocr_extractions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tipo_doc          text NOT NULL REFERENCES ocr_doc_types(codigo),
    json              jsonb NOT NULL,
    confianca         real,
    status_validacao  text NOT NULL DEFAULT 'pendente'
                      CHECK (status_validacao IN ('pendente', 'aprovado', 'revisar', 'corrigido', 'rejeitado')),
    falhas            jsonb NOT NULL DEFAULT '[]'::jsonb,
    revisado_por      uuid REFERENCES users(id),
    criada_em         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ocr_training_samples (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo_doc        text NOT NULL REFERENCES ocr_doc_types(codigo),
    extraction_id   uuid REFERENCES ocr_extractions(id) ON DELETE SET NULL,
    caminho_imagem  text NOT NULL,
    json_correto    jsonb NOT NULL,
    criada_em       timestamptz NOT NULL DEFAULT now()
);
