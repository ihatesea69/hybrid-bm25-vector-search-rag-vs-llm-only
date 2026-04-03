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
  sectionType?: string;
  retrievalPath?: string;
  bm25Meta?: Record<string, unknown> | null;
  vectorMeta?: Record<string, unknown> | null;
  rerankerMeta?: Record<string, unknown> | null;
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
  };
  hybridRag: {
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
