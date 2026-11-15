/**
 * Helpers for the Google ID token that authenticates calls to the backend.
 *
 * Google ID tokens live for one hour, far shorter than the NextAuth session,
 * so the token is refreshed on the server (see `auth.ts`) shortly before it
 * expires. This module is shared by the server and the browser.
 */

/** How long before expiry the server swaps the ID token for a fresh one. */
export const ID_TOKEN_REFRESH_MARGIN_SECONDS = 10 * 60

/**
 * Read the `exp` claim (seconds since the epoch) of a JWT.
 *
 * The token is NOT verified here — the backend does that. This only tells us
 * when to ask Google for a new one.
 */
export function idTokenExpiry(idToken: string): number | undefined {
  try {
    const payload = idToken.split(".")[1]
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/")
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")
    const exp: unknown = JSON.parse(atob(padded)).exp
    return typeof exp === "number" ? exp : undefined
  } catch {
    return undefined
  }
}
