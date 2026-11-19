"use client";

import { all } from "iso-3166-1";
import dynamic from "next/dynamic";
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Coordinates, TrialSelection, TrialSite } from "./trial-map";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const noSites: Trial[] = [];
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));

type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type CityLookup = { places: City[]; error?: string };
type SearchError = { message: string; standaloneUrl?: string };
type Trial = TrialSite & {
  title: string | null;
  city: string | null;
  country: string | null;
  overall_status: string;
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
  const [selection, setSelection] = useState<TrialSelection | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<SearchError | null>(null);
  const searchButton = useRef<HTMLButtonElement>(null);
  const searchCondition = condition.trim();
  const selectedCity = location && "name" in location ? location : null;
  const showCitySuggestions = !location && city.trim().length >= 2;
  let emptyResultsMessage = "Enter a condition and location to get started.";
  if (location) {
    emptyResultsMessage = searchCondition.length < 2
      ? "Enter a condition, then select Search trials."
      : "Select Search trials to find nearby sites.";
  }
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
    setSelection(null);
    setError(null);
  }

  function locate() {
    setError(null);
    const embedded = window.self !== window.top;
    const embeddedLocationError = {
      message: "Location access was blocked here. Open Find Trials in its own tab to try again, or choose a city.",
      standaloneUrl: window.location.href,
    };
    if (!navigator.geolocation) {
      setError(embedded ? embeddedLocationError : {
        message: "Current location is unavailable in this browser. Choose a city.",
      });
      return;
    }
    setBusy("Getting your location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        const coordinates = { latitude, longitude };
        setLocation(coordinates);
        setCity("");
        setCountryCode("");
        setCityLookup(null);
        if (searchCondition.length >= 2) {
          void searchTrials(coordinates);
        } else {
          setResults(null);
          setSelection(null);
          setBusy("");
        }
      },
      (failure) => {
        setBusy("");
        if (failure.code === failure.PERMISSION_DENIED) {
          setError(embedded ? embeddedLocationError : {
            message: "Allow location for this site in your browser settings and try again, or choose a city.",
          });
        } else if (failure.code === failure.TIMEOUT) {
          setError({ message: "The location request timed out. Try again or choose a city." });
        } else {
          setError({ message: "Your device couldn’t determine its location. Check your device’s location settings or choose a city." });
        }
      },
      { timeout: 10000 },
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (searchCondition.length < 2) {
      setError({ message: "Enter a condition with at least two characters." });
      return;
    }
    if (!location) {
      setError({ message: "Choose a city from the suggestions, or use your current location." });
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
      setError({ message: "Couldn’t search for trials. Try again in a moment." });
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
                onChange={(event) => { setCondition(event.target.value); setError(null); }} />
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
                    setError(null);
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
          {error && (
            <p className="error" role="alert">
              {error.message} {error.standaloneUrl && (
                <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
              )}
            </p>
          )}
        </form>
        <div className="explorer">
          <section className="results" aria-labelledby="results-heading">
            <h2 id="results-heading">Trial results</h2>
            <p className="result-status" role="status">{busy || (results
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
    </>
  );
}
