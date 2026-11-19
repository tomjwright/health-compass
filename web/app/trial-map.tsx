"use client";

import { circleMarker, latLngBounds, map, tileLayer, type LatLngTuple } from "leaflet";
import { useEffect, useRef } from "react";

export type Coordinates = {
  latitude: number;
  longitude: number;
};

export type TrialSite = Coordinates & {
  distance_km: number;
  facility: string | null;
  site_status: string | null;
  nct_id: string;
};

const localRadiusKm = 50;
const maxFocusSites = 5;
const maxFocusZoom = 12;

export default function TrialMap({ sites, origin }: { sites: TrialSite[]; origin: Coordinates | null }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    const center: LatLngTuple | null = origin ? [origin.latitude, origin.longitude] : null;
    const view = map(container.current, { scrollWheelZoom: false });
    view.setView(center ?? [20, 8], center ? maxFocusZoom : 2);
    tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(view);

    const bounds = latLngBounds(center ? [center] : []);
    if (center) {
      circleMarker(center, { radius: 12, color: "#1d4ed8", fillColor: "#dbeafe", fillOpacity: 1 })
        .addTo(view).bindTooltip("Search location");
    }

    const nearest = [...sites].sort((left, right) => left.distance_km - right.distance_km);
    const nearby = nearest.filter((site) => site.distance_km <= localRadiusKm).slice(0, maxFocusSites);
    const focusSites = new Set(nearby.length ? nearby : nearest.slice(0, 1));
    for (const site of sites) {
      const color = site.site_status === "RECRUITING" ? "#08705b" : "#526784";
      // Plot across the antimeridian using the world copy closest to the search location.
      const worldOffset = center ? Math.round((center[1] - site.longitude) / 360) * 360 : 0;
      const point: LatLngTuple = [site.latitude, site.longitude + worldOffset];
      const label = document.createElement("span");
      label.textContent = site.facility ?? site.nct_id;
      circleMarker(point, { radius: 8, color: "#fff", fillColor: color, fillOpacity: 1 })
        .addTo(view).bindTooltip(label);
      if (!center || focusSites.has(site)) bounds.extend(point);
    }
    if (bounds.isValid()) view.fitBounds(bounds, { padding: [28, 28], maxZoom: maxFocusZoom, animate: false });
    return () => { view.remove(); };
  }, [sites, origin]);

  return <div ref={container} className="trial-map" role="region" aria-label="Trial site map" />;
}
