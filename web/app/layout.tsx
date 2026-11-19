import type { Metadata } from "next";
import "@fontsource-variable/dm-sans";
import "leaflet/dist/leaflet.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Find clinical trials | Health Compass",
  description: "Search ClinicalTrials.gov for studies by condition and location.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
