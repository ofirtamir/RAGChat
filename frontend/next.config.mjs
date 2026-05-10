/** @type {import('next').NextConfig} */

// In production the frontend calls the backend directly via NEXT_PUBLIC_BACKEND_ORIGIN.
// The rewrite below is only active during local development (when the env var is not set).
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
