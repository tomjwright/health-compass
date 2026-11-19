"use client";

import { all } from "iso-3166-1";
import dynamic from "next/dynamic";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Coordinates, TrialSelection, TrialSite } from "./trial-map";
import { mappedSite, TrialResult, type Trial } from "./trial-results";

const TrialMap = dynamic(() => import("./trial-map"), {
  ssr: false,
  loading: () => <div className="trial-map" aria-hidden="true" />,
});
const registryCountryNames: Record<string, string> = {
  AG: "Antigua and Barbuda", AX: "Aland Islands", BA: "Bosnia and Herzegovina",
  BQ: "Bonaire, Saint Eustatius and Saba ", BS: "The Bahamas",
  CD: "Democratic Republic of the Congo", CG: "Republic of the Congo", CW: "Curacao",
  CV: "Cabo Verde", GM: "The Gambia", HK: "Hong Kong", KN: "Saint Kitts and Nevis",
  LC: "Saint Lucia", MF: "Saint Martin", MM: "Burma", MO: "Macau", RE: "Reunion",
  TF: "French Southern and Antarctic Lands", TR: "Turkey (Türkiye)", TT: "Trinidad and Tobago",
  UM: "United States Minor Outlying Islands", VA: "Holy See",
  VC: "Saint Vincent and the Grenadines", VI: "Virgin Islands",
};
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));

type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type CityLookup = { places: City[]; error?: string };
type LocationAction = "nearby" | "compare";
type SearchError = {
  message: string;
  standaloneUrl?: string;
  placement?: "comparison" | "pagination";
  canBrowse?: boolean;
};
type Results = {
  trials: Trial[];
  total_count: number | null;
  next_page_token: string | null;
  radius_km: number | null;
};

function cityLabel(city: City) {
  return [...new Set([city.name, city.admin1, city.country].filter(Boolean))].join(", ");
}

export default function FindTrials() {
  const [condition, setCondition] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [city, setCity] = useState("");
  const [location, setLocation] = useState<Coordinates | City | null>(null);
  const [radius, setRadius] = useState("100");
  const [comparisonOrigin, setComparisonOrigin] = useState<Coordinates | null>(null);
  const [showDistanceScale, setShowDistanceScale] = useState(false);
  const [cityLookup, setCityLookup] = useState<CityLookup | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [selection, setSelection] = useState<TrialSelection | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<SearchError | null>(null);
  const searchButton = useRef<HTMLButtonElement>(null);
  const searchCondition = condition.trim();
  const countryName = countries.find(({ code }) => code === countryCode)?.name;
  const selectedCity = location && "name" in location ? location : null;
  const showCitySuggestions = !location && city.trim().length >= 2;
  const radiusKm = results?.radius_km ?? null;
  const distanceOrigin = location ?? comparisonOrigin;
  const distanceScaleKm = showDistanceScale && distanceOrigin ? (radiusKm ?? 20_000) : null;
  const scope = radiusKm != null
    ? `within ${radiusKm.toLocaleString("en")} km of ${selectedCity ? cityLabel(selectedCity) : "your location"}${countryName ? ` · ${countryName} only` : ""}`
    : countryName ? `in ${countryName}` : "worldwide";
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
      const leftDistance = sitesByTrial.get(left.nct_id)?.distance_km ?? Number.MAX_VALUE;
      const rightDistance = sitesByTrial.get(right.nct_id)?.distance_km ?? Number.MAX_VALUE;
      return leftDistance - rightDistance;
    });
  }, [results, radiusKm, distanceOrigin, sitesByTrial]);
  const sites = useMemo(() => trials.flatMap(({ nct_id }) => {
    const site = sitesByTrial.get(nct_id);
    return site ? [site] : [];
  }), [trials, sitesByTrial]);
  const showMap = sites.length > 0 || (location != null && radiusKm != null);
  const showBrowseAction = searchCondition.length >= 2 && (radiusKm != null || error?.canBrowse);
  let cityMessage = "Looking up cities…";
  if (cityLookup) {
    cityMessage = cityLookup.error ?? (cityLookup.places.length ? "Choose a city." : "No matching cities.");
  }
  let resultMessage = "";
  if (results) {
    const { trials, total_count } = results;
    const total = total_count == null ? "" : ` of ${total_count}`;
    if (trials.length) {
      resultMessage = `Showing ${trials.length}${total}.`;
    } else if (radiusKm != null) {
      resultMessage = "No recruiting studies found within this cutoff. Try a larger distance or another location.";
    } else {
      resultMessage = "No recruiting studies found.";
    }
  } else if (!error) {
    resultMessage = searchCondition.length >= 2 ? "Select Search trials to see matching studies." : "Enter a condition to get started.";
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

  function clearSearch() {
    setComparisonOrigin(null);
    setShowDistanceScale(false);
    setResults(null);
    setSelection(null);
    setError(null);
  }

  function editLocation() {
    setLocation(null);
    setRadius("100");
    setCityLookup(null);
    clearSearch();
  }

  function locate(action: LocationAction) {
    setError(null);
    if (action === "nearby") {
      setResults(null);
      setSelection(null);
    }
    function reportError(locationError: SearchError) {
      setError({
        ...locationError,
        placement: action === "compare" ? "comparison" : undefined,
        canBrowse: action === "nearby",
      });
    }
    const embedded = window.self !== window.top;
    const embeddedLocationError = {
      message: "Location access was blocked here. Open Find Trials in its own tab to try again, or choose a city.",
      standaloneUrl: window.location.href,
    };
    if (!navigator.geolocation) {
      reportError(embedded ? embeddedLocationError : {
        message: "Current location is unavailable in this browser. Choose a city.",
      });
      return;
    }
    setBusy("Getting your location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        const coordinates = { latitude, longitude };
        if (action === "compare") {
          setComparisonOrigin(coordinates);
          setShowDistanceScale(true);
          setSelection(null);
          setBusy("");
          return;
        }
        setLocation(coordinates);
        setComparisonOrigin(null);
        setCity("");
        setCityLookup(null);
        const cutoff = Number(radius);
        if (searchCondition.length >= 2 && Number.isInteger(cutoff) && cutoff >= 1 && cutoff <= 1000) {
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
          reportError(embedded ? embeddedLocationError : {
            message: "Allow location for this site in your browser settings and try again, or choose a city.",
          });
        } else if (failure.code === failure.TIMEOUT) {
          reportError({ message: "The location request timed out. Try again or choose a city." });
        } else {
          reportError({ message: "Your device couldn’t determine its location. Check your device’s location settings or choose a city." });
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
    if (city.trim() && !selectedCity) {
      setError({ message: "Choose a city from the suggestions, or clear the city field." });
      return;
    }
    if (!location && !countryCode) {
      locate("nearby");
      return;
    }
    void searchTrials(location);
  }

  async function searchTrials(origin: Coordinates | null, {
    pageToken = null,
    browse = false,
  }: { pageToken?: string | null; browse?: boolean } = {}) {
    if (!pageToken && !browse) setResults(null);
    setSelection(null);
    setError(null);
    setBusy("Searching for trials…");
    try {
      const response = await fetch("/api/trials/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          condition: searchCondition,
          country: countryName ? (registryCountryNames[countryCode] ?? countryName) : null,
          origin: origin && { latitude: origin.latitude, longitude: origin.longitude },
          ...(origin && { radius_km: Number(radius) }),
          page_token: pageToken,
        }),
      });
      if (!response.ok) throw new Error("Trial search failed");
      const page: Results = await response.json();
      setResults((previous) => pageToken && previous ? {
        ...page,
        trials: [...previous.trials, ...page.trials],
        total_count: previous.total_count,
      } : page);
    } catch {
      setError({
        message: "Couldn’t search for trials. Try again in a moment.",
        placement: pageToken ? "pagination" : undefined,
        canBrowse: browse,
      });
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <header><span className="brand-mark" aria-hidden="true">✦</span> Health Compass</header>
      <main>
        <h1>Find clinical trials</h1>
        <p className="intro">Search near your current location or a city you choose. You can also browse by country.</p>
        <form onSubmit={submit} autoComplete="off">
          <fieldset disabled={Boolean(busy)}>
            <label>Condition
              <input value={condition} maxLength={100} placeholder="e.g. Alopecia"
                onChange={(event) => {
                  setCondition(event.target.value);
                  if (!selectedCity) {
                    setLocation(null);
                    setRadius("100");
                  }
                  clearSearch();
                }} />
            </label>
            <label>Country (optional)
              <select value={countryCode} onChange={(event) => {
                setCountryCode(event.target.value);
                editLocation();
              }}>
                <option value="">Any country</option>
                {countries.map(({ code, name }) => <option key={code} value={code}>{name}</option>)}
              </select>
            </label>
            <label>City (optional)
              <input value={city} maxLength={100} placeholder={location ? "Current location selected" : "e.g. Singapore"}
                onChange={(event) => { setCity(event.target.value); editLocation(); }} />
            </label>
            {showCitySuggestions && (
              <div className="city-options" aria-label="Matching cities" role="group">
                <p role="status">{cityMessage}</p>
                {cityLookup?.places.map((place) => (
                  <button className="secondary" key={place.id} type="button" onClick={() => {
                    setLocation(place);
                    setComparisonOrigin(null);
                    setShowDistanceScale(false);
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
            <div className="locate-button">
              <button type="button" className="secondary" onClick={() => locate("nearby")}>Use current location</button>
            </div>
            {location && <div className="radius-field">
              <label>Distance cutoff (km)
                <input type="number" min={1} max={1000} step={1} required value={radius}
                  aria-describedby="radius-range" onChange={(event) => {
                    setRadius(event.target.value);
                    setResults(null);
                    setSelection(null);
                    setError(null);
                  }} />
              </label>
              <span id="radius-range">1–1,000 km</span>
            </div>}
            <button type="submit" className="search-button" ref={searchButton}>Search trials</button>
          </fieldset>
          {error && !error.placement && (
            <p className="error" role="alert">
              {error.message} {error.standaloneUrl && (
                <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
              )}
            </p>
          )}
        </form>
        <section className={showMap ? "explorer" : "explorer list-only"} aria-labelledby="results-heading">
          <div className="results-heading">
            <div>
              <h2 id="results-heading">{results ? `Recruiting studies ${scope}` : "Trial results"}</h2>
              <p className="result-status" role="status">{busy || resultMessage}</p>
            </div>
            {(sites.length > 0 || showBrowseAction) && <div className="result-actions">
              {sites.length > 0 && (
                <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => {
                  if (distanceOrigin) setShowDistanceScale(!showDistanceScale);
                  else locate("compare");
                }}>
                  {showDistanceScale ? "Hide distance scale" : `Show distance from ${selectedCity?.name ?? "me"}`}
                </button>
              )}
              {showBrowseAction && (
                <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void searchTrials(null, { browse: true })}>
                  {countryName ? `Browse studies in ${countryName}` : "Browse worldwide studies"}
                </button>
              )}
            </div>}
            {error?.placement === "comparison" && <p className="error results-error" role="alert">
              {error.message} {error.standaloneUrl && (
                <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
              )}
            </p>}
          </div>
          {showMap && <div className="map-panel">
            <TrialMap sites={sites} origin={distanceOrigin} selection={selection} radiusKm={radiusKm} />
            <div className="map-key">
              {sites.length > 0 && <span><i /> One pin per mapped study</span>}
              {distanceOrigin && <span><i className="origin-marker" /> {selectedCity ? "Selected city" : "Your location"}</span>}
              {radiusKm != null && <span><i className="radius-marker" /> {radiusKm.toLocaleString("en")} km cutoff</span>}
            </div>
          </div>}
          {results && results.trials.length > 0 && (
            <div className="results">
              {distanceScaleKm != null && (
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
              {error?.placement === "pagination" && <p className="error" role="alert">{error.message}</p>}
              {results.next_page_token && <button type="button" className="secondary" disabled={Boolean(busy)}
                onClick={() => void searchTrials(null, { pageToken: results.next_page_token })}>Load more studies</button>}
            </div>
          )}
        </section>
        <footer>Confirm recruiting status and eligibility with the study team.</footer>
      </main>
    </>
  );
}
