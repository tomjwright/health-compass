"use client";

import { all } from "iso-3166-1";
import { useEffect, useRef, useState } from "react";
import type { Coordinates } from "./trial-map";

type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
type Location = Coordinates | City;
type CityLookup = { places: City[]; error?: string };
type LocationError = { message: string; standaloneUrl?: string };
type Props = {
  onChange: (location: Coordinates | null, fromDevice?: boolean) => void;
  onLocating: (pending: boolean) => void;
};

const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));

function cityLabel(city: City) {
  return [...new Set([city.name, city.admin1, city.country].filter(Boolean))].join(", ");
}

export default function LocationFields({ onChange, onLocating }: Props) {
  const [countryCode, setCountryCode] = useState("");
  const [city, setCity] = useState("");
  const [location, setLocation] = useState<Location | null>(null);
  const [lookup, setLookup] = useState<CityLookup | null>(null);
  const [error, setError] = useState<LocationError | null>(null);
  const unmounted = useRef(false);
  const selectedCity = location && "name" in location ? location : null;
  const lookingUpCity = !location && city.trim().length >= 2;

  useEffect(() => {
    unmounted.current = false;
    return () => { unmounted.current = true; };
  }, []);

  useEffect(() => {
    if (!lookingUpCity) return;
    const controller = new AbortController();
    const delay = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/cities", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ city, countryCode }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("City lookup failed");
        const places: City[] = await response.json();
        if (!controller.signal.aborted) setLookup({ places });
      } catch {
        if (!controller.signal.aborted) {
          setLookup({ places: [], error: "City lookup is temporarily unavailable." });
        }
      }
    }, 300);
    return () => {
      window.clearTimeout(delay);
      controller.abort();
    };
  }, [city, countryCode, lookingUpCity]);

  function edit() {
    setLocation(null);
    setLookup(null);
    setError(null);
    onChange(null);
  }

  function locate() {
    setError(null);
    const embedded = window.self !== window.top;
    const embeddedError = {
      message: "Location access was blocked here. Open this page in its own tab to try again, or choose a city.",
      standaloneUrl: window.location.href,
    };
    if (!navigator.geolocation) {
      setError(embedded ? embeddedError : {
        message: "Current location is unavailable in this browser. Choose a city.",
      });
      return;
    }
    onLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords: { latitude, longitude } }) => {
        if (unmounted.current) return;
        const coordinates = { latitude, longitude };
        setLocation(coordinates);
        setCity("");
        setCountryCode("");
        setLookup(null);
        onLocating(false);
        onChange(coordinates, true);
      },
      (failure) => {
        if (unmounted.current) return;
        onLocating(false);
        if (failure.code === failure.PERMISSION_DENIED) {
          setError(embedded ? embeddedError : {
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

  return (
    <>
      <label>Country
        <select value={countryCode} onChange={(event) => { setCountryCode(event.target.value); edit(); }}>
          <option value="">Any country</option>
          {countries.map(({ code, name }) => <option key={code} value={code}>{name}</option>)}
        </select>
      </label>
      <label>City
        <input value={city} maxLength={100} placeholder={location ? "Current location selected" : "e.g. Singapore"}
          onChange={(event) => { setCity(event.target.value); edit(); }} />
      </label>
      {lookingUpCity && (
        <div className="city-options" aria-label="Matching cities" role="group">
          <p role="status">{lookup
            ? lookup.error ?? (lookup.places.length ? "Choose a city." : "No matching cities.")
            : "Looking up cities…"}</p>
          {lookup?.places.map((place) => (
            <button className="secondary" key={place.id} type="button" onClick={() => {
              setLocation(place);
              setCity(place.name);
              setLookup(null);
              setError(null);
              onChange(place);
            }}>{cityLabel(place)}</button>
          ))}
          {Boolean(lookup?.places.length) && <a href="https://www.geonames.org/">City data by GeoNames</a>}
        </div>
      )}
      {selectedCity && (
        <div className="city-options">
          <span>{cityLabel(selectedCity)}</span>
          <a href="https://www.geonames.org/">City data by GeoNames</a>
        </div>
      )}
      <button type="button" className="secondary locate-button" onClick={locate}>Use current location</button>
      {error && <p className="error location-error" role="alert">
        {error.message} {error.standaloneUrl && (
          <a href={error.standaloneUrl} target="_blank" rel="noopener noreferrer">Open in a new tab</a>
        )}
      </p>}
    </>
  );
}
