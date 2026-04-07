export type MetricSummary = {
  batch_id?: string;
  mode?: string;
  rows?: number;
  skipped_unjudged?: number;
  "recall@10"?: number;
  "mrr@10"?: number;
  "ndcg@10"?: number;
  map?: number;
  faithfulness?: number;
  correctness?: number;
  relevancy?: number;
  "unique_docs@k"?: number;
  "duplicate_chunks@k"?: number;
  left_wins?: number;
  left_win_rate?: number;
  hybrid_rag_wins?: number;
  hybrid_rag_win_rate?: number;
};

export type SummaryPayload = {
  summary: Record<string, MetricSummary>;
  failure_cases: Record<string, Array<Record<string, unknown>>>;
};

export type PhaseStat = {
  label: string;
  value: string;
};

export type PhaseDetail = {
  id: string;
  title: string;
  summary: string;
  status: "ready" | "partial" | "degraded";
  stats: PhaseStat[];
  outputs: string[];
  details: string[];
};

export type PhaseSnapshotPayload = {
  updatedAt: string;
  phases: PhaseDetail[];
};

export type KbSummary = {
  status: "ok" | "degraded";
  documents: number;
  nodes: number;
  embeddedNodes: number;
  contextualEmbeddedNodes: number;
  retrievalRuns: number;
  answerRuns: number;
  availableModes: string[];
  artifacts: Record<string, boolean>;
};

export type HealthPayload = {
  ok: boolean;
  timestamp: string;
  retrieval: Record<string, unknown>;
  evaluation: {
    artifacts?: Record<string, boolean>;
  };
};

export type DemoResultRow = {
  nodeId?: string;
  docId?: string;
  sourceId?: string;
  title: string;
  score: number;
  snippet: string;
  rawBody?: string;
  contextSummary?: string | null;
  sectionType?: string;
  retrievalPath?: string;
  chunkIndex?: number;
  chunkCount?: number;
  tokenCount?: number;
  charCount?: number;
  bm25Meta?: Record<string, unknown> | null;
  vectorMeta?: Record<string, unknown> | null;
  rerankerMeta?: Record<string, unknown> | null;
};

export type SearchConfig = {
  top_k?: number;
  candidate_k?: number | null;
  chunk_level?: boolean;
  reranker?: Record<string, unknown>;
};

export type Citation = {
  citationId?: number;
  nodeId?: string;
  docId?: string;
  sourceId?: string;
  title: string;
};

export type DemoQueryPayload = {
  batchId: string;
  queryText: string;
  topK: number;
  hybrid: {
    mode: string;
    results: DemoResultRow[];
    config?: SearchConfig;
  };
  contextualHybrid: {
    mode: string;
    results: DemoResultRow[];
    config?: SearchConfig;
  };
  hybridRag: {
    mode: string;
    answerText: string;
    citations: Citation[];
    evidenceBundle: DemoResultRow[];
  };
  contextualHybridRag: {
    mode: string;
    answerText: string;
    citations: Citation[];
    evidenceBundle: DemoResultRow[];
  };
  llmOnly: {
    mode: string;
    answerText: string;
  };
  timingsMs: Record<string, number>;
};
