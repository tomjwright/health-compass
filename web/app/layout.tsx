import type { Metadata } from "next";
import Link from "next/link";
import "@fontsource-variable/dm-sans";
import "leaflet/dist/leaflet.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Health Compass | Clinical trials",
  description: "Find nearby studies and ask questions about clinical trials.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <span className="brand-mark" aria-hidden="true">✦</span> Health Compass
          <nav aria-label="Main navigation">
            <Link href="/">Find Trials</Link>
            <Link href="/ask">Ask</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
