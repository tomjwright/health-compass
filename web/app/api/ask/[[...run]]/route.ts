const api = process.env.HEALTH_COMPASS_API_URL ?? "http://127.0.0.1:8000";
const privateJson = { "Cache-Control": "no-store", "Content-Type": "application/json" };
type Context = { params: Promise<{ run?: string[] }> };

async function forward(request: Request, path: string) {
  try {
    const upstream = await fetch(`${api}/ask${path}`, {
      method: request.method,
      body: request.method === "POST" ? await request.text() : undefined,
      headers: privateJson,
      cache: "no-store",
      signal: request.signal,
    });
    return new Response(upstream.body, { status: upstream.status, headers: privateJson });
  } catch {
    return Response.json({ error: "The agent API is unavailable." }, { status: 502, headers: privateJson });
  }
}

export async function POST(request: Request, { params }: Context) {
  if ((await params).run?.length) {
    return Response.json({ error: "Invalid request path." }, { status: 404, headers: privateJson });
  }
  return forward(request, "");
}

export async function GET(request: Request, { params }: Context) {
  const run = (await params).run;
  if (run?.length !== 1 || !/^[\w-]+$/.test(run[0])) {
    return Response.json({ error: "Invalid run ID." }, { status: 404, headers: privateJson });
  }
  return forward(request, `/${run[0]}`);
}
