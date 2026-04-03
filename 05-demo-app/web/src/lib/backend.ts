import type { HealthPayload, KbSummary, PhaseSnapshotPayload, SummaryPayload } from "@/lib/types";

const BACKEND_BASE_URL = process.env.MEDIR_DEMO_API_URL ?? "http://127.0.0.1:8008";

export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const request = new Request(`${BACKEND_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
  });
  return fetch(request);
}

async function safeJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const response = await backendFetch(path);
    if (!response.ok) {
      return fallback;
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

export function getHealthSnapshot(): Promise<HealthPayload> {
  return safeJson<HealthPayload>("/health", {
    ok: false,
    timestamp: new Date(0).toISOString(),
    retrieval: {},
    evaluation: { artifacts: {} },
  });
}

export function getKbSummarySnapshot(): Promise<KbSummary> {
  return safeJson<KbSummary>("/kb/summary", {
    status: "degraded",
    documents: 0,
    nodes: 0,
    embeddedNodes: 0,
    retrievalRuns: 0,
    answerRuns: 0,
    availableModes: ["hybrid", "hybrid_rag", "llm_only"],
    artifacts: {},
  });
}

export function getDemoSummarySnapshot(): Promise<SummaryPayload> {
  return safeJson<SummaryPayload>("/demo/summary", {
    summary: {},
    failure_cases: {},
  });
}

export function getDemoPhasesSnapshot(): Promise<PhaseSnapshotPayload> {
  return safeJson<PhaseSnapshotPayload>("/demo/phases", {
    updatedAt: new Date(0).toISOString(),
    phases: [],
  });
}
