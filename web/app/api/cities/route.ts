import { whereAlpha2 } from "iso-3166-1";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const name = typeof body?.city === "string" ? body.city.trim() : "";
  const countryCode = body?.countryCode ?? "";
  if (name.length < 2 || name.length > 100) {
    return Response.json({ error: "Enter a city name between 2 and 100 characters." }, { status: 400 });
  }
  if (typeof countryCode !== "string" || (countryCode && !whereAlpha2(countryCode))) {
    return Response.json({ error: "Enter a valid two-letter country code." }, { status: 400 });
  }

  const query = new URLSearchParams({ name, count: "10" });
  if (countryCode) query.set("countryCode", countryCode.toUpperCase());
  try {
    const response = await fetch(`https://geocoding-api.open-meteo.com/v1/search?${query}`, {
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]),
    });
    if (!response.ok) throw new Error("City lookup failed");
    const { results = [] } = await response.json();
    const cities = results.filter((place: { feature_code?: string }) => place.feature_code?.startsWith("PPL"));
    return Response.json(cities.slice(0, 5));
  } catch {
    return Response.json({ error: "City lookup is temporarily unavailable." }, { status: 502 });
  }
}
