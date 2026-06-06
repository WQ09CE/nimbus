import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Stream large media uploads straight through to the backend. Unlike the
// catch-all rewrite, this passes the request body as a stream (duplex: "half")
// so multi-GB videos are never buffered in the Next process.
export async function POST(
  request: NextRequest,
  { params }: { params: { sessionId: string } }
) {
  const nimbusUrl = process.env.NIMBUS_API_URL || "http://localhost:4096";
  const targetUrl = `${nimbusUrl}/api/v1/sessions/${params.sessionId}/upload`;

  const headers: Record<string, string> = {
    "Content-Type": request.headers.get("Content-Type") || "application/octet-stream",
  };
  const filename = request.headers.get("X-Filename");
  if (filename) headers["X-Filename"] = filename;

  const upstreamRes = await fetch(targetUrl, {
    method: "POST",
    headers,
    body: request.body,
    // @ts-expect-error -- Node.js fetch needs duplex for a streaming request body
    duplex: "half",
  });

  const body = await upstreamRes.text();
  return new Response(body, {
    status: upstreamRes.status,
    headers: { "Content-Type": upstreamRes.headers.get("Content-Type") || "application/json" },
  });
}
