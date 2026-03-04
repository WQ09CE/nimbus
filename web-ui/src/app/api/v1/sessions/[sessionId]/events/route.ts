import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  { params }: { params: { sessionId: string } }
) {
  const nimbusUrl = process.env.NIMBUS_API_URL || "http://localhost:4096";
  const targetUrl = `${nimbusUrl}/api/v1/sessions/${params.sessionId}/events`;

  const headers: Record<string, string> = {};
  const lastEventId = request.headers.get("Last-Event-ID");
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }
  const reqId = request.headers.get("X-Request-ID");
  if (reqId) {
    headers["X-Request-ID"] = reqId;
  }

  const upstreamRes = await fetch(targetUrl, {
    method: "GET",
    headers,
  });

  if (!upstreamRes.ok || !upstreamRes.body) {
    return new Response(upstreamRes.body || upstreamRes.statusText, {
      status: upstreamRes.status,
      headers: { "Content-Type": "text/plain" },
    });
  }

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
