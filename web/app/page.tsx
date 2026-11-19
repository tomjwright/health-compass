"use client";

import dynamic from "next/dynamic";
import { type FormEvent, useRef, useState } from "react";
import LocationFields from "./location-fields";
import type { Coordinates, TrialSelection, TrialSite } from "./trial-map";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const noSites: Trial[] = [];
type Trial = TrialSite & {
  title: string | null;
  city: string | null;
  country: string | null;
  overall_status: string;
};
type Results = { trials: Trial[]; data_as_of: string };

function status(code: string) {
  const label = code.toLowerCase().replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export default function FindTrials() {
  const [condition, setCondition] = useState("");
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [locating, setLocating] = useState(false);
  const [results, setResults] = useState<Results | null>(null);
  const [selection, setSelection] = useState<TrialSelection | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const searchButton = useRef<HTMLButtonElement>(null);
  const searchCondition = condition.trim();
  let emptyResultsMessage = "Enter a condition and location to get started.";
  if (location) {
    emptyResultsMessage = searchCondition.length < 2
      ? "Enter a condition, then select Search trials."
      : "Select Search trials to find nearby sites.";
  }
  function changeLocation(coordinates: Coordinates | null, fromDevice?: boolean) {
    setLocation(coordinates);
    setResults(null);
    setSelection(null);
    setError(null);
    if (coordinates && fromDevice && searchCondition.length >= 2) {
      void searchTrials(coordinates);
    } else if (coordinates && !fromDevice) {
      searchButton.current?.focus();
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (searchCondition.length < 2) {
      setError("Enter a condition with at least two characters.");
      return;
    }
    if (!location) {
      setError("Choose a city from the suggestions, or use your current location.");
      return;
    }
    void searchTrials(location);
  }

  async function searchTrials({ latitude, longitude }: Coordinates) {
    setResults(null);
    setSelection(null);
    setBusy("Searching for trials…");
    try {
      const response = await fetch("/api/trials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ condition: searchCondition, latitude, longitude }),
      });
      if (!response.ok) throw new Error("Trial search failed");
      setResults(await response.json());
    } catch {
      setError("Couldn’t search for trials. Try again in a moment.");
    } finally {
      setBusy("");
    }
  }

  return (
    <main>
      <h1>Find clinical trials</h1>
      <p className="intro">Search by condition and location to see the closest matching trial sites.</p>
      <form onSubmit={submit} autoComplete="off">
        <fieldset disabled={locating || Boolean(busy)}>
          <label>Condition
            <input value={condition} maxLength={100} placeholder="e.g. Alopecia"
              onChange={(event) => { setCondition(event.target.value); setError(null); }} />
          </label>
          <LocationFields onChange={changeLocation} onLocating={setLocating} />
          <button type="submit" className="search-button" ref={searchButton}>Search trials</button>
        </fieldset>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
      <div className="explorer">
        <section className="results" aria-labelledby="results-heading">
          <h2 id="results-heading">Trial results</h2>
          <p className="result-status" role="status">{(locating ? "Getting your location…" : busy) || (results
            ? `${results.trials.length} matching ${results.trials.length === 1 ? "trial" : "trials"} shown`
            : error ? "" : emptyResultsMessage)}</p>
          {results && <p className="freshness">ClinicalTrials.gov data as of {results.data_as_of.slice(0, 10)}</p>}
          <ul className="trial-list">
            {results?.trials.map((trial) => (
              <li key={trial.nct_id}>
                <span className={trial.site_status === "RECRUITING" ? "badge recruiting" : "badge"}>
                  {trial.site_status ? `Site · ${status(trial.site_status)}` : "Site status unavailable"}
                </span>
                <h3><button type="button" className="trial-focus"
                  aria-pressed={selection?.nctId === trial.nct_id}
                  onClick={() => setSelection({ nctId: trial.nct_id })}>
                  {trial.title ?? trial.nct_id}
                </button></h3>
                <p>{[trial.facility, trial.city, trial.country].filter(Boolean).join(", ") || "Site name unavailable"} · {trial.distance_km.toLocaleString("en")} km</p>
                <p><a href={`https://clinicaltrials.gov/study/${encodeURIComponent(trial.nct_id)}`}>
                  {trial.nct_id} on ClinicalTrials.gov
                </a> · Trial status {status(trial.overall_status)}</p>
              </li>
            ))}
          </ul>
        </section>
        <TrialMap sites={results?.trials ?? noSites} origin={location} selection={selection} />
      </div>
      <footer>Confirm recruiting status and eligibility with the study team.</footer>
    </main>
  );
}
