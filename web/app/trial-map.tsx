"use client";

import { circleMarker, latLngBounds, map, tileLayer } from "leaflet";
import { useEffect, useRef } from "react";

export type TrialSite = {
  latitude: number;
  longitude: number;
  facility: string | null;
  site_status: string | null;
  nct_id: string;
};

export default function TrialMap({ sites }: { sites: TrialSite[] }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    const view = map(container.current, { scrollWheelZoom: false }).setView([20, 8], 2);
    tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(view);

    const bounds = latLngBounds([]);
    for (const site of sites) {
      const color = site.site_status === "RECRUITING" ? "#08705b" : "#526784";
      const point: [number, number] = [site.latitude, site.longitude];
      const label = document.createElement("span");
      label.textContent = site.facility ?? site.nct_id;
      circleMarker(point, { radius: 8, color: "#fff", fillColor: color, fillOpacity: 1 })
        .addTo(view).bindTooltip(label);
      bounds.extend(point);
    }
    if (bounds.isValid()) view.fitBounds(bounds, { padding: [28, 28], maxZoom: 12 });
    return () => { view.remove(); };
  }, [sites]);

  return <div ref={container} className="trial-map" role="region" aria-label="Trial site map" />;
}
