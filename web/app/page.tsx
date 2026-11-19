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
type SearchError = { message: string; standaloneUrl?: string; placement?: "comparison" | "pagination" };
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
  const distanceScaleKm = radiusKm ?? (comparisonOrigin ? 20_000 : null);
  const scope = radiusKm != null
    ? `within ${radiusKm.toLocaleString("en")} km of ${selectedCity ? cityLabel(selectedCity) : "your location"}${countryName ? ` · ${countryName} only` : ""}`
    : countryName ? `in ${countryName}` : "worldwide";
  const sitesByTrial = useMemo(() => {
    const sites = new Map<string, TrialSite>();
    for (const trial of results?.trials ?? []) {
      const site = mappedSite(trial, comparisonOrigin);
      if (site) sites.set(trial.nct_id, site);
    }
    return sites;
  }, [results, comparisonOrigin]);
  const sites = useMemo(() => [...sitesByTrial.values()], [sitesByTrial]);
  const showMap = sites.length > 0 || (location != null && radiusKm != null);
  const canCompare = radiusKm == null && sites.length > 0;
  let cityMessage = "Looking up cities…";
  if (cityLookup) {
    cityMessage = cityLookup.error ?? (cityLookup.places.length ? "Choose a city." : "No matching cities.");
  }
  let resultMessage = "";
  if (results) {
    const { trials, total_count } = results;
    const total = total_count == null ? "" : ` of ${total_count}`;
    resultMessage = trials.length ? `Showing ${trials.length}${total}.` : "No recruiting studies found.";
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

  function editLocation(keepDeviceLocation = false) {
    if (!keepDeviceLocation || selectedCity) setLocation(null);
    setCityLookup(null);
    setResults(null);
    setSelection(null);
    setError(null);
  }

  function locate(compareDistances = false) {
    setError(null);
    function reportError(locationError: SearchError) {
      setError({ ...locationError, placement: compareDistances ? "comparison" : undefined });
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
        if (compareDistances) {
          setComparisonOrigin(coordinates);
          setSelection(null);
          setBusy("");
          return;
        }
        setLocation(coordinates);
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
    void searchTrials(location);
  }

  async function searchTrials(origin: Coordinates | null, pageToken: string | null = null) {
    if (!pageToken) setResults(null);
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
      setError({ message: "Couldn’t search for trials. Try again in a moment.", placement: pageToken ? "pagination" : undefined });
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <header><span className="brand-mark" aria-hidden="true">✦</span> Health Compass</header>
      <main>
        <h1>Find clinical trials</h1>
        <p className="intro">Search recruiting studies worldwide, or add a location to narrow your results.</p>
        <form onSubmit={submit} autoComplete="off">
          <fieldset disabled={Boolean(busy)}>
            <label>Condition
              <input value={condition} maxLength={100} placeholder="e.g. Alopecia"
                onChange={(event) => {
                  setCondition(event.target.value);
                  setResults(null);
                  setSelection(null);
                  setError(null);
                }} />
            </label>
            <label>Country (optional)
              <select value={countryCode} onChange={(event) => {
                setCountryCode(event.target.value);
                editLocation(true);
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
              {location && !selectedCity
                ? <button type="button" className="secondary" onClick={() => editLocation()}>Clear current location</button>
                : <button type="button" className="secondary" onClick={() => locate()}>Use current location</button>}
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
            {canCompare && (
              <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => {
                if (comparisonOrigin) {
                  setComparisonOrigin(null);
                  setSelection(null);
                } else {
                  locate(true);
                }
              }}>{comparisonOrigin ? "Hide distances" : "Show distance from me"}</button>
            )}
            {error?.placement === "comparison" && <p className="error results-error" role="alert">
              {error.message} {error.standaloneUrl && (
                <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
              )}
            </p>}
          </div>
          {showMap && <div className="map-panel">
            <TrialMap sites={sites} origin={location ?? comparisonOrigin} selection={selection} radiusKm={radiusKm} />
            <div className="map-key">
              {sites.length > 0 && <span><i /> One pin per mapped study</span>}
              {(location || comparisonOrigin) && <span><i className="origin-marker" /> {selectedCity ? "Selected city" : "Your location"}</span>}
              {radiusKm != null && <span><i className="radius-marker" /> {radiusKm.toLocaleString("en")} km cutoff</span>}
            </div>
          </div>}
          {results && results.trials.length > 0 && (
            <div className="results">
              {distanceScaleKm != null && (
                <div className="distance-scale">
                  <p>{radiusKm == null ? "Straight-line distance to the closest mapped site" : "Closest recruiting sites first · straight-line distance"}</p>
                  <div><span>0 km</span><span>{distanceScaleKm.toLocaleString("en")} km</span></div>
                </div>
              )}
              <ul className="trial-list">
                {results.trials.map((trial) => <TrialResult key={trial.nct_id} trial={trial}
                  site={sitesByTrial.get(trial.nct_id)} scaleKm={distanceScaleKm}
                  selected={selection?.nctId === trial.nct_id} onSelect={() => setSelection({ nctId: trial.nct_id })} />)}
              </ul>
              {error?.placement === "pagination" && <p className="error" role="alert">{error.message}</p>}
              {results.next_page_token && <button type="button" className="secondary" disabled={Boolean(busy)}
                onClick={() => void searchTrials(location, results.next_page_token)}>Load more studies</button>}
            </div>
          )}
        </section>
        <footer>Confirm recruiting status and eligibility with the study team.</footer>
      </main>
    </>
  );
}
