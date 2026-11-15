"use client"

import { useEffect, useRef } from "react"
import { SessionProvider, useSession } from "next-auth/react"
import { ID_TOKEN_REFRESH_MARGIN_SECONDS, idTokenExpiry } from "@/lib/id-token"

/**
 * Keeps the Google ID token held by the browser fresh.
 *
 * The server renews the token whenever the session is fetched close to its
 * expiry (see `auth.ts`), but the browser only fetches the session on load and
 * on tab focus. Once the token enters the renewal window, re-fetch the session
 * every minute until a new token arrives.
 */
function IdTokenKeepAlive() {
  const { data: session, update } = useSession()
  const updateRef = useRef(update)
  updateRef.current = update
  const idToken = session?.idToken

  useEffect(() => {
    if (!idToken) return
    const expiresAt = idTokenExpiry(idToken)
    if (!expiresAt) return
    // Start 30s inside the server's renewal window so the fetch is sure to renew.
    const renewFrom = expiresAt - ID_TOKEN_REFRESH_MARGIN_SECONDS + 30
    const timer = setInterval(() => {
      if (Date.now() / 1000 >= renewFrom) void updateRef.current()
    }, 60_000)
    return () => clearInterval(timer)
  }, [idToken])

  return null
}

export function AuthSessionProvider({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <IdTokenKeepAlive />
      {children}
    </SessionProvider>
  )
}
