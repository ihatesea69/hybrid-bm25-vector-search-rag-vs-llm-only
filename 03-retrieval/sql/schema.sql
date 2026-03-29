CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;

CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_kind TEXT,
    title TEXT,
    text_path TEXT,
    source_uri TEXT,
    mime_type TEXT,
    language TEXT,
    trust_level TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS kb_nodes (
    node_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    source_id TEXT,
    title TEXT,
    body TEXT NOT NULL,
    parser TEXT,
    order_idx INTEGER,
    parent_node_id TEXT,
    level INTEGER,
    token_count INTEGER,
    section_type TEXT,
    embedding VECTOR(1536),
    node_meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS kb_nodes_doc_idx ON kb_nodes (doc_id);
CREATE INDEX IF NOT EXISTS kb_nodes_source_idx ON kb_nodes (source_id);
CREATE INDEX IF NOT EXISTS kb_nodes_embedding_idx
ON kb_nodes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_nodes_bm25_idx
ON kb_nodes USING bm25 (body) WITH (text_config = 'english');

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode TEXT NOT NULL,
    query_id TEXT,
    query_text TEXT NOT NULL,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS answer_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode TEXT NOT NULL,
    query_id TEXT,
    query_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_bundle JSONB NOT NULL DEFAULT '[]'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS answer_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode TEXT NOT NULL,
    query_id TEXT,
    evaluator TEXT NOT NULL,
    score DOUBLE PRECISION,
    passing BOOLEAN,
    feedback TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS comparison_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    query_id TEXT,
    left_mode TEXT NOT NULL,
    right_mode TEXT NOT NULL,
    preferred_left BOOLEAN,
    score DOUBLE PRECISION,
    reasoning TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
