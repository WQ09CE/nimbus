import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
    const searchParams = req.nextUrl.searchParams;
    const ref = searchParams.get("ref");

    if (!ref) {
        return NextResponse.json({ error: "Missing 'ref' parameter" }, { status: 400 });
    }

    try {
        const nimfsUrl = process.env.NIMBUS_API_URL || "http://localhost:8000";

        // Convert the nimfs:// prefix to a valid HTTP API path for the python backend
        // Assuming backend endpoint is something like /api/v1/artifacts/:id
        // But for now, since we don't have the exact python backend endpoint for artifacts yet,
        // let's return a simulated response if we can't fetch it.

        // In a real implementation, we would map nimfs://artifact/123 -> http://localhost:8000/api/v1/artifacts/123
        const artifactId = ref.replace("nimfs://artifact/", "");

        const response = await fetch(`${nimfsUrl}/api/v1/artifacts/${artifactId}`, {
            headers: {
                "Content-Type": "application/json",
            },
        });

        if (!response.ok) {
            if (response.status === 404) {
                return NextResponse.json({ error: "Artifact not found" }, { status: 404 });
            }
            throw new Error(`Backend responded with status: ${response.status}`);
        }

        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("[Artifacts API] Fetch error:", error);
        return NextResponse.json(
            { error: "Failed to fetch artifact", details: (error as Error).message },
            { status: 500 }
        );
    }
}
