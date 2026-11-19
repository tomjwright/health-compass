import type { NextConfig } from "next";

const api = process.env.HEALTH_COMPASS_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/trials", destination: `${api}/trials` }];
  },
};

export default config;
