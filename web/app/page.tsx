"use client";

import { all } from "iso-3166-1";
import dynamic from "next/dynamic";
import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Coordinates, TrialSelection, TrialSite } from "./trial-map";
import { mappedSite, TrialResult, type Trial } from "./trial-results";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));
const defaultRadius = 100;
const maxRadius = 10_000;
const radiusError = `Enter a distance from 1 to ${maxRadius.toLocaleString("en")} km.`;

type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type CityLookup = { places: City[]; error?: string };
type Location = Coordinates | City;
type SearchArea = { location: Location; radiusKm: number };
type TrialQuery = { condition: string; area: SearchArea | null };
type SearchMode = "nearby" | "all";
type SearchError = { message: string; standaloneUrl?: string; retry?: TrialQuery };
type Results = {
  trials: Trial[];
  total_count: number | null;
  next_page_token: string | null;
  radius_km: number | null;
};

function cityLabel(city: City) {
  return [...new Set([city.name, city.admin1, city.country].filter(Boolean))].join(", ");
}

function cityAt(location: Location | null) {
  return location && "id" in location ? location : null;
}

function distanceLimit(value: string) {
  const cutoff = Number(value);
  return Number.isInteger(cutoff) && cutoff >= 1 && cutoff <= maxRadius ? cutoff : null;
}

export default function FindTrials() {
  const [condition, setCondition] = useState("");
  const [submittedCondition, setSubmittedCondition] = useState("");
  const [mode, setMode] = useState<SearchMode>("nearby");
  const [countryCode, setCountryCode] = useState("");
  const [city, setCity] = useState("");
  const [location, setLocation] = useState<Location | null>(null);
  const [radius, setRadius] = useState(String(defaultRadius));
  const [cityLookup, setCityLookup] = useState<CityLookup | null>(null);
  const [view, setView] = useState<{ query: TrialQuery; results: Results } | null>(null);
  const [selection, setSelection] = useState<TrialSelection | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<SearchError | null>(null);
  const request = useRef<AbortController | null>(null);
  const radiusTimer = useRef<number | undefined>(undefined);
  const locationAttempt = useRef(0);
  const latestRadius = useRef<number | null>(defaultRadius);
  const cityField = useRef<HTMLInputElement>(null);

  const searchCondition = condition.trim();
  const selectedCity = cityAt(location);
  const showCitySuggestions = mode === "nearby" && !selectedCity && city.trim().length >= 2;
  const results = view?.results;
  const nearbyArea = view?.query.area;
  const radiusKm = results?.radius_km ?? null;
  const distanceOrigin = nearbyArea?.location ?? location;
  const referenceCity = cityAt(distanceOrigin);
  const distanceScaleKm = distanceOrigin ? (radiusKm ?? 20_000) : null;
  const retryQuery = error?.retry;
  const scope = nearbyArea
    ? `within ${nearbyArea.radiusKm.toLocaleString("en")} km of ${referenceCity ? cityLabel(referenceCity) : "your location"}`
    : "worldwide";
  const sitesByTrial = useMemo(() => {
    const sites = new Map<string, TrialSite>();
    for (const trial of results?.trials ?? []) {
      const site = mappedSite(trial, distanceOrigin);
      if (site) sites.set(trial.nct_id, site);
    }
    return sites;
  }, [results, distanceOrigin]);
  const trials = useMemo(() => {
    const found = results?.trials ?? [];
    if (radiusKm != null || !distanceOrigin) return found;
    return [...found].sort((left, right) => {
      const leftDistance = sitesByTrial.get(left.nct_id)?.distance_km ?? Infinity;
      const rightDistance = sitesByTrial.get(right.nct_id)?.distance_km ?? Infinity;
      return leftDistance - rightDistance;
    });
  }, [results, radiusKm, distanceOrigin, sitesByTrial]);
  const sites = useMemo(() => trials.flatMap(({ nct_id }) => {
    const site = sitesByTrial.get(nct_id);
    return site ? [site] : [];
  }), [trials, sitesByTrial]);
  let resultMessage = "";
  if (results) {
    if (trials.length) {
      resultMessage = `Showing ${trials.length}${results.total_count == null ? "" : ` of ${results.total_count}`}.`;
    } else if (nearbyArea) {
      resultMessage = "No recruiting studies found within this radius. Try a larger distance or another location.";
    } else {
      resultMessage = "No recruiting studies found.";
    }
  } else if (!error) {
    resultMessage = "Enter a condition to find recruiting studies.";
  }

  useEffect(() => {
    return () => {
      request.current?.abort();
      window.clearTimeout(radiusTimer.current);
      locationAttempt.current += 1;
    };
  }, []);

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
        if (!controller.signal.aborted) setCityLookup({ places: [], error: "City lookup is temporarily unavailable." });
      }
    }
    const delay = window.setTimeout(lookup, 300);
    return () => {
      window.clearTimeout(delay);
      controller.abort();
    };
  }, [city, countryCode, showCitySuggestions]);

  function cancelTrialSearch() {
    request.current?.abort();
    window.clearTimeout(radiusTimer.current);
    setBusy("");
  }

  function cancelPending() {
    cancelTrialSearch();
    locationAttempt.current += 1;
  }

  function searchTrials(query: TrialQuery, { pageToken = null, debounce = false }: {
    pageToken?: string | null;
    debounce?: boolean;
  } = {}) {
    cancelTrialSearch();
    const controller = new AbortController();
    request.current = controller;
    setError(null);
    setBusy(query.area ? "Updating nearby trials…" : "Searching worldwide…");
    async function fetchTrials() {
      const { area } = query;
      try {
        const response = await fetch("/api/trials/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            condition: query.condition,
            origin: area && { latitude: area.location.latitude, longitude: area.location.longitude },
            ...(area && { radius_km: area.radiusKm }),
            page_token: pageToken,
          }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Trial search failed");
        const page: Results = await response.json();
        if (controller.signal.aborted) return;
        setSelection(null);
        setView(previous => ({
          query,
          results: pageToken && previous ? {
            ...page,
            trials: [...previous.results.trials, ...page.trials],
            total_count: previous.results.total_count,
          } : page,
        }));
      } catch {
        if (!controller.signal.aborted) {
          setError({ message: "Couldn’t search for trials. Try again in a moment.", retry: pageToken ? undefined : query });
        }
      } finally {
        if (!controller.signal.aborted) setBusy("");
      }
    }
    if (debounce) radiusTimer.current = window.setTimeout(fetchTrials, 250);
    else void fetchTrials();
  }

  function locate(phrase: string, searchMode: SearchMode = "nearby") {
    const nearby = searchMode === "nearby";
    if (nearby) {
      cancelPending();
      setError(null);
      setBusy("Getting your location…");
    }
    const attempt = ++locationAttempt.current;
    const blocked = window.self !== window.top ? {
      message: "Location access was blocked here. Open Find Trials in its own tab to try again, or choose a city.",
      standaloneUrl: window.location.href,
    } : null;
    if (!navigator.geolocation) {
      if (nearby) {
        setBusy("");
        setError(blocked ?? { message: "Current location is unavailable in this browser. Choose a city." });
      }
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        if (attempt !== locationAttempt.current) return;
        const origin = { latitude, longitude };
        setLocation(origin);
        if (!nearby) return;
        setCity("");
        setCityLookup(null);
        const radiusKm = latestRadius.current;
        if (phrase.length >= 2 && radiusKm != null) {
          searchTrials({ condition: phrase, area: { location: origin, radiusKm } });
        } else {
          setBusy("");
        }
      },
      (failure) => {
        if (!nearby || attempt !== locationAttempt.current) return;
        setBusy("");
        if (failure.code === failure.PERMISSION_DENIED) {
          setError(blocked ?? { message: "Allow location for this site in your browser settings, or choose a city." });
        } else if (failure.code === failure.TIMEOUT) {
          setError({ message: "The location request timed out. Try again or choose a city." });
        } else {
          setError({ message: "Your device couldn’t determine its location. Check your device’s location settings or choose a city." });
        }
      },
      { timeout: 10000 },
    );
  }

  function searchWorldwide(phrase: string) {
    searchTrials({ condition: phrase, area: null });
    if (!location) locate(phrase, "all");
  }

  function submitNearby(phrase: string) {
    if (city.trim() && !selectedCity) {
      setError({ message: "Choose a city from the suggestions, or use your current location." });
      cityField.current?.focus();
      return;
    }
    const radiusKm = distanceLimit(radius);
    if (radiusKm == null) {
      setError({ message: radiusError });
    } else if (location) {
      searchTrials({ condition: phrase, area: { location, radiusKm } });
    } else {
      locate(phrase);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (searchCondition.length < 2) {
      setError({ message: "Enter a condition with at least two characters." });
      return;
    }
    setSubmittedCondition(searchCondition);
    if (mode === "nearby") submitNearby(searchCondition);
    else searchWorldwide(searchCondition);
  }

  function changeMode(next: SearchMode) {
    if (next === mode) return;
    cancelPending();
    setMode(next);
    setError(null);
    if (searchCondition.length < 2) return;
    setSubmittedCondition(searchCondition);
    if (next === "all") searchWorldwide(searchCondition);
    else submitNearby(searchCondition);
  }

  function editLocation() {
    cancelPending();
    setLocation(null);
    setCityLookup(null);
    setError(null);
  }

  function chooseCity(place: City) {
    cancelPending();
    setLocation(place);
    setCity(place.name);
    setCityLookup(null);
    setError(null);
    cityField.current?.focus();
    if (searchCondition.length < 2) return;
    setSubmittedCondition(searchCondition);
    const radiusKm = distanceLimit(radius);
    if (radiusKm != null) searchTrials({ condition: searchCondition, area: { location: place, radiusKm } });
    else setError({ message: radiusError });
  }

  function changeRadius(value: string) {
    setRadius(value);
    const radiusKm = distanceLimit(value);
    latestRadius.current = radiusKm;
    if (radiusKm == null) {
      cancelPending();
      setError({ message: radiusError });
    } else if (submittedCondition && location) {
      searchTrials({ condition: submittedCondition, area: { location, radiusKm } }, { debounce: true });
    } else {
      setError(null);
    }
  }

  return (
    <>
      <header><span className="brand-mark" aria-hidden="true">✦</span> Health Compass</header>
      <main>
        <h1>Find clinical trials</h1>
        <p className="intro">
          Explore recruiting studies near you or search worldwide. <Link href="/ask">Ask Health Compass</Link>
        </p>
        <form onSubmit={submit} autoComplete="off" className="condition-search">
          <label>Condition
            <input value={condition} maxLength={100} placeholder="e.g. Alopecia"
              onChange={(event) => {
                cancelPending();
                setCondition(event.target.value);
                setSubmittedCondition("");
                if (!selectedCity) setLocation(null);
                setView(null);
                setSelection(null);
                setError(null);
              }} />
          </label>
          <button type="submit">Search trials</button>
        </form>
        <section className="explorer" aria-labelledby="results-heading">
          <div className="results-heading">
            <div>
              <h2 id="results-heading">{results ? `Recruiting studies ${scope}` : "Trial results"}</h2>
              <p className="result-status" role="status">{busy || resultMessage}</p>
            </div>
            <div className="search-modes" role="group" aria-label="Where to search">
              <button type="button" aria-pressed={mode === "nearby"} onClick={() => changeMode("nearby")}>Nearby</button>
              <button type="button" aria-pressed={mode === "all"} onClick={() => changeMode("all")}>All trials</button>
            </div>
            {error && <div className="results-error">
              <p className="error" role="alert">
                {error.message} {error.standaloneUrl && (
                  <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
                )}
              </p>
              {retryQuery && <button type="button" className="secondary" onClick={() => searchTrials(retryQuery)}>Retry search</button>}
            </div>}
          </div>
          <div className="map-panel">
            {mode === "nearby" && <section className="map-controls" aria-labelledby="locality-heading">
              <div className="locality-heading">
                <div>
                  <h3 id="locality-heading">Search area</h3>
                  {location && <p>{selectedCity ? cityLabel(selectedCity) : "Current location"}</p>}
                </div>
                <button type="button" className="secondary" onClick={() => {
                  if (searchCondition.length >= 2) setSubmittedCondition(searchCondition);
                  locate(searchCondition);
                }}>Use current location</button>
              </div>
              <div className="city-fields">
                <label>Country for city lookup
                  <select value={countryCode} onChange={(event) => {
                    setCountryCode(event.target.value);
                    editLocation();
                  }}>
                    <option value="">Any country</option>
                    {countries.map(({ code, name }) => <option key={code} value={code}>{name}</option>)}
                  </select>
                </label>
                <label>Search near a city
                  <input ref={cityField} value={city} maxLength={100} placeholder="e.g. Singapore"
                    autoComplete="off" onChange={(event) => { setCity(event.target.value); editLocation(); }} />
                </label>
              </div>
              {showCitySuggestions && (
                <div className="city-options" aria-label="Matching cities" role="group">
                  <p role="status">{cityLookup?.error ?? (cityLookup ? cityLookup.places.length ? "Choose a city." : "No matching cities." : "Looking up cities…")}</p>
                  {cityLookup?.places.map(place => (
                    <button className="secondary" key={place.id} type="button" onClick={() => chooseCity(place)}>{cityLabel(place)}</button>
                  ))}
                  {Boolean(cityLookup?.places.length) && <a href="https://www.geonames.org/">City data by GeoNames</a>}
                </div>
              )}
              <div className="radius-control">
                <label htmlFor="radius-slider">Search radius</label>
                <input id="radius-slider" type="range" min={1} max={maxRadius} step={1}
                  value={distanceLimit(radius) ?? defaultRadius} onChange={event => changeRadius(event.target.value)} />
                <div className="radius-number">
                  <input type="number" min={1} max={maxRadius} step={1} value={radius}
                    aria-label="Search radius in kilometres" aria-invalid={distanceLimit(radius) == null}
                    onChange={event => changeRadius(event.target.value)} />
                  <span>km</span>
                </div>
              </div>
            </section>}
            <TrialMap sites={sites} origin={distanceOrigin} selection={selection} radiusKm={radiusKm} />
            <div className="map-key">
              {sites.length > 0 && <span><i /> One pin per mapped study</span>}
              {distanceOrigin && <span><i className="origin-marker" /> {referenceCity ? "Selected city" : "Your location"}</span>}
            </div>
          </div>
          <div className="results">
            {distanceScaleKm != null && sites.length > 0 && (
              <div className="distance-scale">
                <p>{radiusKm == null ? "Loaded studies, nearest first · straight-line distance" : "Closest recruiting sites first · straight-line distance"}</p>
                <div><span>0 km</span><span>{distanceScaleKm.toLocaleString("en")} km</span></div>
              </div>
            )}
            <ul className="trial-list">
              {trials.map((trial) => <TrialResult key={trial.nct_id} trial={trial}
                site={sitesByTrial.get(trial.nct_id)} scaleKm={distanceScaleKm}
                selected={selection?.nctId === trial.nct_id} onSelect={() => setSelection({ nctId: trial.nct_id })} />)}
            </ul>
            {view && results?.next_page_token && <button type="button" className="secondary" disabled={Boolean(busy)}
              onClick={() => searchTrials(view.query, { pageToken: results.next_page_token })}>Load more studies</button>}
          </div>
        </section>
        <footer>Confirm recruiting status and eligibility with the study team.</footer>
      </main>
    </>
  );
}
