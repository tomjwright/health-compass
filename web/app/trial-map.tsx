"use client";

import { circleMarker, latLngBounds, map, tileLayer, type CircleMarker, type LatLngTuple, type Map as LeafletMap } from "leaflet";
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
const maxFocusZoom = 11;
const siteZoom = 10;

export type TrialSelection = { nctId: string };

export default function TrialMap({ sites, origin, selection }: {
  sites: TrialSite[];
  origin: Coordinates | null;
  selection: TrialSelection | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapState = useRef<{ view: LeafletMap; markers: Map<string, CircleMarker> } | null>(null);

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
    const markers = new Map<string, CircleMarker>();
    for (const site of sites) {
      const color = site.site_status === "RECRUITING" ? "#08705b" : "#526784";
      // Plot across the antimeridian using the world copy closest to the search location.
      const worldOffset = center ? Math.round((center[1] - site.longitude) / 360) * 360 : 0;
      const point: LatLngTuple = [site.latitude, site.longitude + worldOffset];
      const label = document.createElement("span");
      label.textContent = site.facility ?? site.nct_id;
      const marker = circleMarker(point, { radius: 8, color: "#fff", fillColor: color, fillOpacity: 1 })
        .addTo(view).bindTooltip(label);
      markers.set(site.nct_id, marker);
      if (!center || focusSites.has(site)) bounds.extend(point);
    }
    if (bounds.isValid()) view.fitBounds(bounds, { padding: [28, 28], maxZoom: maxFocusZoom, animate: false });
    mapState.current = { view, markers };
    return () => {
      mapState.current = null;
      view.remove();
    };
  }, [sites, origin]);

  useEffect(() => {
    const state = mapState.current;
    if (!state || !selection) return;
    const marker = state.markers.get(selection.nctId);
    if (!marker) return;
    const { view } = state;
    view.setView(marker.getLatLng(), Math.max(view.getZoom(), siteZoom), { animate: false });
    marker.openTooltip();
    container.current?.scrollIntoView({ block: "nearest" });
    return () => { marker.closeTooltip(); };
  }, [selection, sites, origin]);

  return <div ref={container} className="trial-map" role="region" aria-label="Trial site map" />;
}
