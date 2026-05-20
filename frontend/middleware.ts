import { auth } from "@/auth"
import { NextResponse } from "next/server"

/**
 * Auth middleware — wraps every non-public route, redirecting unauthenticated
 * users to /login with the original destination preserved via ?callbackUrl=…
 *
 * The default `export { auth as middleware }` only attaches the session; it
 * does NOT redirect. We have to handle the redirect explicitly.
 */
export default auth((req) => {
  if (!req.auth) {
    const url = new URL("/login", req.nextUrl.origin)
    url.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search)
    return NextResponse.redirect(url)
  }
  return NextResponse.next()
})

// Protect everything except the login page, NextAuth API routes, static assets,
// and Next.js internals.
export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico|opengraph-image).*)"],
}
