const api = process.env.HEALTH_COMPASS_API_URL ?? "http://127.0.0.1:8000";
const headers = { "Content-Type": "application/json", "Cache-Control": "no-store" };

export async function POST(request: Request, { params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params;
  if (resource !== "approvals" && resource !== "research") {
    return Response.json({ detail: "Search unavailable." }, { status: 404, headers });
  }
  try {
    const upstream = await fetch(`${api}/${resource}`, {
      method: "POST", headers, body: await request.text(), cache: "no-store", signal: request.signal,
    });
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Search temporarily unavailable." }, { status: 502, headers });
  }
}
