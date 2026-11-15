let redirecting = false

/**
 * Send the user to the login page because their session can no longer be used
 * to call the backend (the Google ID token expired and could not be refreshed,
 * or the backend answered 401).
 *
 * The login page needs a click to start the Google flow, so this can never
 * turn into an automatic redirect loop.
 */
export function redirectToSignIn(): void {
  if (redirecting || typeof window === "undefined") return
  redirecting = true
  const here = window.location.pathname + window.location.search
  const params = new URLSearchParams({ reason: "expired", callbackUrl: here })
  window.location.assign(`/login?${params}`)
}
