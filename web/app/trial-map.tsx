"use client";

import { circleMarker, control, latLngBounds, layerGroup, map, tileLayer, type CircleMarker, type LatLngTuple, type LayerGroup, type Map as LeafletMap } from "leaflet";
import { useEffect, useRef } from "react";

export type Coordinates = {
  latitude: number;
  longitude: number;
};

export type TrialSite = Coordinates & {
  distance_km: number | null;
  facility: string | null;
  city: string | null;
  country: string | null;
  nct_id: string;
};

const localRadiusKm = 50;
const maxFocusSites = 5;
const maxFocusZoom = 11;
const siteZoom = 9;

export type TrialSelection = { nctId: string };

export default function TrialMap({ sites, origin, selection, radiusKm }: {
  sites: TrialSite[];
  origin: Coordinates | null;
  selection: TrialSelection | null;
  radiusKm: number | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapState = useRef<{ view: LeafletMap; layers: LayerGroup; markers: Map<string, CircleMarker> } | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const view = map(container.current, { scrollWheelZoom: false });
    view.on("focus", () => { view.scrollWheelZoom.enable(); });
    view.on("blur", () => { view.scrollWheelZoom.disable(); });
    view.setView([20, 8], 2);
    control.scale({ imperial: false }).addTo(view);
    tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(view);
    const resize = new ResizeObserver(() => view.invalidateSize({ debounceMoveend: true }));
    resize.observe(container.current);
    mapState.current = { view, layers: layerGroup().addTo(view), markers: new Map() };
    return () => {
      resize.disconnect();
      mapState.current = null;
      view.remove();
    };
  }, []);

  useEffect(() => {
    const state = mapState.current;
    if (!state) return;
    const { view, layers, markers } = state;
    layers.clearLayers();
    markers.clear();
    const center: LatLngTuple | null = origin ? [origin.latitude, origin.longitude] : null;
    const bounds = latLngBounds(center ? [center] : []);
    if (center) {
      circleMarker(center, { radius: 12, color: "#1d4ed8", fillColor: "#dbeafe", fillOpacity: 1 })
        .addTo(layers).bindTooltip(radiusKm != null ? "Search location" : "Distance reference");
    }
    const focusNearby = radiusKm != null && radiusKm <= 100;
    const nearest = [...sites].sort((left, right) => (left.distance_km ?? Infinity) - (right.distance_km ?? Infinity));
    const nearby = nearest.filter((site) => (site.distance_km ?? Infinity) <= localRadiusKm).slice(0, maxFocusSites);
    const focusSites = new Set(nearby.length ? nearby : nearest.slice(0, 1));
    const referenceLongitude = center?.[1] ?? sites[0]?.longitude ?? 0;
    for (const site of sites) {
      const worldOffset = Math.round((referenceLongitude - site.longitude) / 360) * 360;
      const point: LatLngTuple = [site.latitude, site.longitude + worldOffset];
      const label = document.createElement("span");
      label.textContent = [...new Set([site.facility, site.city, site.country].filter(Boolean))].join(", ") || site.nct_id;
      const marker = circleMarker(point, { radius: 8, color: "#fff", fillColor: "#08705b", fillOpacity: 1 })
        .addTo(layers).bindTooltip(label);
      markers.set(site.nct_id, marker);
      if (!focusNearby || focusSites.has(site)) bounds.extend(point);
    }
    if (center && sites.length === 0) {
      view.setView(center, siteZoom, { animate: false });
    } else if (bounds.isValid()) {
      view.fitBounds(bounds, { padding: [28, 28], maxZoom: maxFocusZoom, animate: false });
    }
  }, [sites, origin, radiusKm]);

  useEffect(() => {
    const state = mapState.current;
    if (!state || !selection) return;
    const marker = state.markers.get(selection.nctId);
    if (!marker) return;
    const { view } = state;
    view.setView(marker.getLatLng(), radiusKm == null ? 7 : siteZoom, { animate: false });
    marker.openTooltip();
    container.current?.scrollIntoView({ block: "nearest" });
    return () => { marker.closeTooltip(); };
  }, [selection, sites, origin, radiusKm]);

  return <div ref={container} className="trial-map" role="region" aria-label="Trial site map" />;
}
