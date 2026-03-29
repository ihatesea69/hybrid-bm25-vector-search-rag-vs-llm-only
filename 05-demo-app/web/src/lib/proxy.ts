import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";

export async function proxyJson(path: string, init?: RequestInit): Promise<NextResponse> {
  try {
    const response = await backendFetch(path, init);
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Backend unavailable.",
      },
      { status: 503 },
    );
  }
}
