"use client";

import dynamic from "next/dynamic";
import { type FormEvent, useState } from "react";
import type { TrialSite } from "./trial-map";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const noSites: Trial[] = [];

type Coordinates = { latitude: number; longitude: number };
type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type Trial = TrialSite & {
  title: string | null;
  city: string | null;
  country: string | null;
  overall_status: string;
  distance_km: number;
};
type Results = { trials: Trial[]; data_as_of: string };

function cityLabel(city: City) {
  return [...new Set([city.name, city.admin1, city.country].filter(Boolean))].join(", ");
}

function status(code: string) {
  const label = code.toLowerCase().replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export default function FindTrials() {
  const [condition, setCondition] = useState("");
  const [city, setCity] = useState("");
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [cities, setCities] = useState<City[]>([]);
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  function locate() {
    setError("");
    setCities([]);
    if (!navigator.geolocation) {
      setError("Current location is unavailable. Enter a city instead.");
      return;
    }
    setBusy("Getting your location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        setLocation({ latitude, longitude });
        setCity("");
        setBusy("");
      },
      () => {
        setError("Couldn’t get your location. Enter a city instead.");
        setBusy("");
      },
      { timeout: 10000 },
    );
  }

  async function search({ latitude, longitude }: Coordinates) {
    setCities([]);
    setError("");
    setResults(null);
    setBusy("Searching for trials…");
    try {
      const response = await fetch("/api/trials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ condition: condition.trim(), latitude, longitude }),
      });
      if (!response.ok) throw new Error("Trial search failed");
      setResults(await response.json());
    } catch {
      setError("Couldn’t search for trials. Try again in a moment.");
    } finally {
      setBusy("");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (condition.trim().length < 2 || (!location && city.trim().length < 2)) {
      setError("Enter a condition and a city, or use your current location.");
      return;
    }
    if (location) return search(location);

    setResults(null);
    setBusy("Looking up your city…");
    try {
      const response = await fetch("/api/cities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ city }),
      });
      if (!response.ok) throw new Error("City lookup failed");
      const places: City[] = await response.json();
      if (places.length === 0) setError("No cities found. Try a nearby city.");
      setCities(places);
    } catch {
      setError("City lookup is temporarily unavailable. Try again or use your current location.");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <header><span className="brand-mark" aria-hidden="true">✦</span> Health Compass</header>
      <main>
        <h1>Find clinical trials</h1>
        <p className="intro">Search by condition and location to see the closest matching trial sites.</p>
        <form onSubmit={submit} autoComplete="off">
          <fieldset disabled={Boolean(busy)}>
            <label>Condition
              <input value={condition} maxLength={100} placeholder="e.g. Alopecia"
                onChange={(event) => { setCondition(event.target.value); setCities([]); }} />
            </label>
            <label>City
              <input value={city} maxLength={100} placeholder={location ? "Current location selected" : "e.g. Singapore"}
                onChange={(event) => { setCity(event.target.value); setLocation(null); setCities([]); }} />
            </label>
            <button type="button" className="secondary" onClick={locate}>Use current location</button>
            <button type="submit">Search trials</button>
          </fieldset>
          {cities.length > 0 && (
            <div className="city-options" aria-label="Matching cities" role="group">
              <p>Choose a city to search nearby.</p>
              {cities.map((place) => (
                <button className="secondary" key={place.id} type="button" onClick={() => {
                  setLocation(place);
                  setCity(cityLabel(place));
                  void search(place);
                }}>{cityLabel(place)}</button>
              ))}
              <a href="https://www.geonames.org/">City data by GeoNames</a>
            </div>
          )}
        </form>
        <div className="explorer">
          <section className="results" aria-labelledby="results-heading">
            <h2 id="results-heading">Trial results</h2>
            <p className="result-status" role="status">{busy || (results
              ? `${results.trials.length} matching ${results.trials.length === 1 ? "trial" : "trials"} shown`
              : error ? "" : "Enter a condition and location to get started.")}</p>
            {error && <p className="error" role="alert">{error}</p>}
            {results && <p className="freshness">ClinicalTrials.gov data as of {results.data_as_of.slice(0, 10)}</p>}
            <ul className="trial-list">
              {results?.trials.map((trial) => (
                <li key={trial.nct_id}>
                  <span className={trial.site_status === "RECRUITING" ? "badge recruiting" : "badge"}>
                    {trial.site_status ? `Site · ${status(trial.site_status)}` : "Site status unavailable"}
                  </span>
                  <h3><a href={`https://clinicaltrials.gov/study/${encodeURIComponent(trial.nct_id)}`}>
                    {trial.title ?? trial.nct_id}
                  </a></h3>
                  <p>{[trial.facility, trial.city, trial.country].filter(Boolean).join(", ") || "Site name unavailable"} · {trial.distance_km.toLocaleString("en")} km</p>
                  <p>{trial.nct_id} · Trial status {status(trial.overall_status)}</p>
                </li>
              ))}
            </ul>
          </section>
          <TrialMap sites={results?.trials ?? noSites} />
        </div>
        <footer>Confirm recruiting status and eligibility with the study team.</footer>
      </main>
    </>
  );
}
