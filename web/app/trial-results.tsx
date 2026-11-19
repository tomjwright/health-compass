import type { Coordinates, TrialSite } from "./trial-map";

type Place = {
  facility: string | null;
  city: string | null;
  country: string | null;
};

export type Trial = {
  nct_id: string;
  title: string;
  nearest_site: (Place & Coordinates & { distance_km: number }) | null;
  recruiting_sites: (Place & { point: { lat: number; lon: number } | null })[];
  studyStatus?: string;
  siteStatus?: string | null;
};

function placeLabel(place: Place) {
  return [...new Set([place.city, place.country].filter(Boolean))].join(", ");
}

function distanceKm(origin: Coordinates, latitude: number, longitude: number) {
  const radians = Math.PI / 180;
  const lat = origin.latitude * radians;
  const siteLat = latitude * radians;
  const lonDifference = (longitude - origin.longitude) * radians;
  const haversine = Math.sin((siteLat - lat) / 2) ** 2
    + Math.cos(lat) * Math.cos(siteLat) * Math.sin(lonDifference / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(Math.min(1, haversine)));
}

export function mappedSite(trial: Trial, comparisonOrigin: Coordinates | null): TrialSite | null {
  if (trial.nearest_site) return { ...trial.nearest_site, nct_id: trial.nct_id };
  let closest: TrialSite | null = null;
  for (const site of trial.recruiting_sites) {
    if (!site.point) continue;
    const { lat: latitude, lon: longitude } = site.point;
    const distance = comparisonOrigin ? distanceKm(comparisonOrigin, latitude, longitude) : null;
    if (!closest || (distance != null && distance < (closest.distance_km ?? Infinity))) {
      closest = {
        nct_id: trial.nct_id, city: site.city, country: site.country, facility: site.facility,
        latitude, longitude, distance_km: distance,
      };
    }
    if (!comparisonOrigin) break;
  }
  return closest;
}

export function TrialResult({ trial, site, scaleKm, selected, onSelect }: {
  trial: Trial;
  site?: TrialSite;
  scaleKm: number | null;
  selected: boolean;
  onSelect: () => void;
}) {
  const place = site ?? trial.recruiting_sites[0];
  const label = place && placeLabel(place);
  const otherPlaces = [...new Set(trial.recruiting_sites.map(placeLabel))]
    .filter((other) => other && other !== label).sort();
  const distance = site?.distance_km;
  const distanceLabel = distance?.toLocaleString("en", { maximumFractionDigits: distance < 10 ? 1 : 0 });
  return (
    <li>
      {trial.studyStatus && <span className="badge">{formatStatus(trial.studyStatus)}</span>}
      <h3>{site ? <button type="button" className="trial-focus" aria-pressed={selected} onClick={onSelect}>
        {trial.title}
      </button> : trial.title}</h3>
      <p className="trial-place">{label || place?.facility || "Recruiting location unavailable"}</p>
      {place?.facility && label && <p>{place.facility}</p>}
      {trial.studyStatus && <p>Site status: {trial.siteStatus ? formatStatus(trial.siteStatus) : "Unavailable"}</p>}
      {otherPlaces.length > 0 && (
        <details className="trial-places">
          <summary>{otherPlaces.length} more {otherPlaces.length === 1 ? "location" : "locations"}</summary>
          <ul>{otherPlaces.map((other) => <li key={other}>{other}</li>)}</ul>
        </details>
      )}
      {scaleKm != null ? (distance != null ? (
        <p className="trial-distance">
          <span className="distance-track" aria-hidden="true">
            <span style={{ width: `${Math.min(100, 100 * distance / scaleKm)}%` }} />
          </span>
          <strong>{distanceLabel} km</strong>
        </p>
      ) : <p>Distance unavailable</p>) : null}
      <p><a href={`https://clinicaltrials.gov/study/${encodeURIComponent(trial.nct_id)}`}>
        {trial.nct_id} on ClinicalTrials.gov
      </a></p>
    </li>
  );
}

function formatStatus(status: string) {
  const words = status.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
