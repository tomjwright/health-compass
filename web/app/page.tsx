"use client";

import { all } from "iso-3166-1";
import dynamic from "next/dynamic";
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { TrialSite } from "./trial-map";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const noSites: Trial[] = [];
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));

type Coordinates = { latitude: number; longitude: number };
type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type CityLookup = { places: City[]; error?: string };
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
  const [countryCode, setCountryCode] = useState("");
  const [city, setCity] = useState("");
  const [location, setLocation] = useState<Coordinates | City | null>(null);
  const [cityLookup, setCityLookup] = useState<CityLookup | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const searchButton = useRef<HTMLButtonElement>(null);
  const selectedCity = location && "name" in location ? location : null;
  const showCitySuggestions = !location && city.trim().length >= 2;
  let cityMessage = "Looking up cities…";
  if (cityLookup) {
    cityMessage = cityLookup.error ?? (cityLookup.places.length ? "Choose a city." : "No matching cities.");
  }

  useEffect(() => {
    if (!showCitySuggestions) return;
    const controller = new AbortController();

    async function lookup() {
      try {
        const response = await fetch("/api/cities", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ city, countryCode }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("City lookup failed");
        const places: City[] = await response.json();
        if (!controller.signal.aborted) setCityLookup({ places });
      } catch {
        if (!controller.signal.aborted) {
          setCityLookup({ places: [], error: "City lookup is temporarily unavailable." });
        }
      }
    }

    const delay = window.setTimeout(lookup, 300);
    return () => {
      window.clearTimeout(delay);
      controller.abort();
    };
  }, [city, countryCode, showCitySuggestions]);

  function editLocation() {
    setLocation(null);
    setCityLookup(null);
    setResults(null);
    setError("");
  }

  function locate() {
    setError("");
    if (!navigator.geolocation) {
      setError("Current location is unavailable. Enter a city instead.");
      return;
    }
    setBusy("Getting your location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        setLocation({ latitude, longitude });
        setCity("");
        setCountryCode("");
        setCityLookup(null);
        setResults(null);
        setBusy("");
      },
      () => {
        setError("Couldn’t get your location. Enter a city instead.");
        setBusy("");
      },
      { timeout: 10000 },
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (condition.trim().length < 2) {
      setError("Enter a condition with at least two characters.");
      return;
    }
    if (!location) {
      setError("Choose a city from the suggestions, or use your current location.");
      return;
    }

    const { latitude, longitude } = location;
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
                onChange={(event) => { setCondition(event.target.value); setError(""); }} />
            </label>
            <label>Country
              <select value={countryCode} onChange={(event) => {
                setCountryCode(event.target.value);
                editLocation();
              }}>
                <option value="">Any country</option>
                {countries.map(({ code, name }) => <option key={code} value={code}>{name}</option>)}
              </select>
            </label>
            <label>City
              <input value={city} maxLength={100} placeholder={location ? "Current location selected" : "e.g. Singapore"}
                onChange={(event) => { setCity(event.target.value); editLocation(); }} />
            </label>
            {showCitySuggestions && (
              <div className="city-options" aria-label="Matching cities" role="group">
                <p role="status">{cityMessage}</p>
                {cityLookup?.places.map((place) => (
                  <button className="secondary" key={place.id} type="button" onClick={() => {
                    setLocation(place);
                    setCity(place.name);
                    setCityLookup(null);
                    setError("");
                    searchButton.current?.focus();
                  }}>{cityLabel(place)}</button>
                ))}
                {Boolean(cityLookup?.places.length) && <a href="https://www.geonames.org/">City data by GeoNames</a>}
              </div>
            )}
            {selectedCity && (
              <div className="city-options">
                <span>{cityLabel(selectedCity)}</span>
                <a href="https://www.geonames.org/">City data by GeoNames</a>
              </div>
            )}
            <button type="button" className="secondary locate-button" onClick={locate}>Use current location</button>
            <button type="submit" className="search-button" ref={searchButton}>Search trials</button>
          </fieldset>
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
