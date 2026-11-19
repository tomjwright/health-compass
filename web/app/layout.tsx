import type { Metadata } from "next";
import Link from "next/link";
import "@fontsource-variable/dm-sans";
import "leaflet/dist/leaflet.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Find clinical trials | Health Compass", template: "%s | Health Compass" },
  description: "Find clinical trials, approved medicines, and published research.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <Link className="brand-link" href="/"><span className="brand-mark" aria-hidden="true">✦</span> Health Compass</Link>
          <nav aria-label="Main navigation">
            <Link href="/">Find trials</Link>
            <Link href="/medicines">Medicines &amp; research</Link>
            <Link href="/status">Service status</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
