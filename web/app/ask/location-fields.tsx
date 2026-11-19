import { all } from "iso-3166-1";
import { useEffect, useState } from "react";
import type { Coordinates } from "../trial-map";

type City = Coordinates & { id: number; name: string; admin1?: string; country?: string };
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countries = all()
  .map(({ alpha2 }) => ({ code: alpha2, name: regionNames.of(alpha2) ?? alpha2 }))
  .sort((left, right) => left.name.localeCompare(right.name, "en"));
const cityLabel = (city: City) => [...new Set([city.name, city.admin1, city.country].filter(Boolean))].join(", ");

export default function LocationFields({ onChange, onLocating }: {
  onChange: (location: Coordinates | null) => void; onLocating: (pending: boolean) => void;
}) {
  const [countryCode, setCountryCode] = useState("");
  const [city, setCity] = useState("");
  const [selected, setSelected] = useState("");
  const [places, setPlaces] = useState<City[] | null>(null);
  const [error, setError] = useState("");
  const looking = !selected && city.trim().length >= 2;

  useEffect(() => {
    if (!looking) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/cities", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ city, countryCode }), signal: controller.signal,
        });
        if (!response.ok) throw new Error("City lookup failed");
        const found: City[] = await response.json();
        if (!controller.signal.aborted) setPlaces(found);
      } catch {
        if (!controller.signal.aborted) setError("City lookup is temporarily unavailable.");
      }
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [city, countryCode, looking]);

  function clear() {
    setPlaces(null);
    setSelected("");
    setError("");
    onChange(null);
  }

  function locate() {
    setError("");
    if (!navigator.geolocation) {
      setError("Device location is unavailable. Choose a city.");
      return;
    }
    onLocating(true);
    navigator.geolocation.getCurrentPosition(({ coords }) => {
      setSelected("Current location");
      setCity("");
      setPlaces(null);
      setError("");
      onChange({ latitude: coords.latitude, longitude: coords.longitude });
      onLocating(false);
    }, () => {
      setError("Couldn’t get your device location. Check browser permissions or choose a city.");
      onLocating(false);
    }, { timeout: 10000 });
  }

  return <>
    <label>Country for city lookup
      <select value={countryCode} onChange={event => { setCountryCode(event.target.value); clear(); }}>
        <option value="">Any country</option>
        {countries.map(({ code, name }) => <option key={code} value={code}>{name}</option>)}
      </select>
    </label>
    <label>City
      <input value={city} maxLength={100} placeholder="e.g. Singapore" onChange={event => { setCity(event.target.value); clear(); }} />
    </label>
    {looking && <div className="city-options" role="group" aria-label="Matching cities">
      {!error && <p role="status">{places ? (places.length ? "Choose a city." : "No matching cities.") : "Looking up cities…"}</p>}
      {places?.map(place => <button className="secondary" type="button" key={place.id} onClick={() => {
        setSelected(cityLabel(place)); setCity(place.name); onChange(place);
      }}>{cityLabel(place)}</button>)}
      {Boolean(places?.length) && <a href="https://www.geonames.org/">City data by GeoNames</a>}
    </div>}
    {selected && <p>Selected location: {selected}</p>}
    <button className="secondary" type="button" onClick={locate}>Use current location</button>
    {selected && <button className="secondary" type="button" onClick={() => { setCity(""); clear(); }}>Clear location</button>}
    {error && <p className="error" role="alert">{error}</p>}
  </>;
}
