-- Extensions for vector support, cryptography, and text search
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;


-- =================================================================
-- Knowledge Base (KB) Tables
-- =================================================================

-- Stores high-level information about each source document.
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    document_text TEXT NOT NULL DEFAULT '',
    document_token_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    section_type TEXT
);

-- Stores individual text chunks (nodes) from documents, along with their embeddings.
CREATE TABLE IF NOT EXISTS kb_nodes (
    node_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    source_id TEXT,
    title TEXT,
    source_url TEXT,
    body TEXT NOT NULL,
    raw_body TEXT NOT NULL DEFAULT '',
    context_summary TEXT,
    contextualized_body TEXT NOT NULL DEFAULT '',
    section_type TEXT,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 1,
    token_count INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    embedding VECTOR(1536),
    contextual_embedding VECTOR(1536)
);

ALTER TABLE kb_documents DROP COLUMN IF EXISTS source_kind;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS text_path;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS source_uri;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS mime_type;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS language;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS trust_level;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS tags;

ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS document_text TEXT NOT NULL DEFAULT '';
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS document_token_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS section_type TEXT;

ALTER TABLE kb_nodes DROP COLUMN IF EXISTS parser;
ALTER TABLE kb_nodes DROP COLUMN IF EXISTS order_idx;
ALTER TABLE kb_nodes DROP COLUMN IF EXISTS parent_node_id;
ALTER TABLE kb_nodes DROP COLUMN IF EXISTS level;
ALTER TABLE kb_nodes DROP COLUMN IF EXISTS node_meta;

ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS raw_body TEXT NOT NULL DEFAULT '';
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS context_summary TEXT;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS contextualized_body TEXT NOT NULL DEFAULT '';
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS chunk_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS token_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS char_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kb_nodes ADD COLUMN IF NOT EXISTS contextual_embedding VECTOR(1536);

UPDATE kb_documents
SET title = COALESCE(title, doc_id),
    document_text = COALESCE(document_text, ''),
    document_token_count = COALESCE(document_token_count, 0),
    chunk_count = COALESCE(chunk_count, 0)
WHERE title IS NULL
   OR document_text IS NULL;

UPDATE kb_nodes
SET source_url = COALESCE(source_url, ''),
    raw_body = COALESCE(NULLIF(raw_body, ''), body),
    contextualized_body = COALESCE(NULLIF(contextualized_body, ''), body),
    chunk_index = COALESCE(chunk_index, 0),
    chunk_count = COALESCE(chunk_count, 1),
    token_count = COALESCE(token_count, 0),
    char_count = COALESCE(char_count, char_length(COALESCE(NULLIF(raw_body, ''), body)))
WHERE raw_body IS NULL
   OR raw_body = ''
   OR contextualized_body IS NULL
   OR contextualized_body = ''
   OR source_url IS NULL
   OR source_url = '';

-- Indexes for the Knowledge Base
CREATE INDEX IF NOT EXISTS kb_nodes_doc_idx ON kb_nodes (doc_id);
CREATE INDEX IF NOT EXISTS kb_nodes_source_idx ON kb_nodes (source_id);
CREATE INDEX IF NOT EXISTS kb_nodes_chunk_doc_idx ON kb_nodes (doc_id, chunk_index);

-- HNSW index for fast vector similarity search (e.g., for semantic retrieval).
CREATE INDEX IF NOT EXISTS kb_nodes_embedding_idx
ON kb_nodes USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS kb_nodes_contextual_embedding_idx
ON kb_nodes USING hnsw (contextual_embedding vector_cosine_ops);

-- BM25 index for efficient full-text keyword search.
CREATE INDEX IF NOT EXISTS kb_nodes_bm25_idx
ON kb_nodes USING bm25 (body) WITH (text_config = 'english');

CREATE INDEX IF NOT EXISTS kb_nodes_contextual_bm25_idx
ON kb_nodes USING bm25 (contextualized_body) WITH (text_config = 'english');


-- =================================================================
-- Experiment Tracking Tables
-- =================================================================

-- Records the results of retrieval runs (e.g., which documents were returned for a query).
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

-- Records the generated answers for a query, including citations and evidence.
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

-- Stores automated or manual evaluations of generated answers.
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

-- Stores pairwise comparisons between different answer generation modes (e.g., A vs. B testing).
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
