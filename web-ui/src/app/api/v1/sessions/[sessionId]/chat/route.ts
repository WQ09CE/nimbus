import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  { params }: { params: { sessionId: string } }
) {
  const nimbusUrl = process.env.NIMBUS_API_URL || "http://localhost:4096";
  const targetUrl = `${nimbusUrl}/api/v1/sessions/${params.sessionId}/chat`;

  const body = await request.text();

  const upstreamRes = await fetch(targetUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(request.headers.get("X-Request-ID")
        ? { "X-Request-ID": request.headers.get("X-Request-ID")! }
        : {}),
    },
    body,
    // @ts-expect-error -- Node.js fetch supports duplex for streaming
    duplex: "half",
  });

  if (!upstreamRes.ok || !upstreamRes.body) {
    return new Response(upstreamRes.body || upstreamRes.statusText, {
      status: upstreamRes.status,
      headers: { "Content-Type": "text/plain" },
    });
  }

  // Pass SSE stream through — requires compress: false in next.config.mjs
  return new Response(upstreamRes.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
