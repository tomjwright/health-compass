import type { Metadata } from "next";

export const metadata: Metadata = { title: "Service status" };
export const dynamic = "force-dynamic";

async function apiIsAvailable() {
  const api = process.env.HEALTH_COMPASS_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${api}/health`, { cache: "no-store", signal: AbortSignal.timeout(4_000) });
    return response.ok && (await response.json()).status === "ok";
  } catch {
    return false;
  }
}

export default async function Status() {
  const available = await apiIsAvailable();
  return (
    <main>
      <h1>Service status</h1>
      <p>Health Compass API: <strong>{available ? "Reachable" : "Unavailable"}</strong></p>
      <p className="freshness">Checked when this page loaded. Searches also depend on their data sources.</p>
    </main>
  );
}
