/** @type {import('next').NextConfig} */

// The frontend always calls the backend directly via NEXT_PUBLIC_BACKEND_ORIGIN
// (set in api.ts), so no /api/* rewrite is needed. A blanket rewrite would
// also swallow /api/auth/* (NextAuth) and proxy it to localhost on Vercel,
// triggering DNS_HOSTNAME_RESOLVED_PRIVATE.
const nextConfig = {};

export default nextConfig;
