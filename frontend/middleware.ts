export { auth as middleware } from "@/auth"

// Protect everything except the login page, NextAuth API routes, static assets,
// and Next.js internals.
export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico|opengraph-image).*)"],
}
