import NextAuth from "next-auth"
import type { JWT } from "next-auth/jwt"
import Google from "next-auth/providers/google"
import { ID_TOKEN_REFRESH_MARGIN_SECONDS, idTokenExpiry } from "@/lib/id-token"

const GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

/**
 * Exchange the stored Google refresh token for a fresh ID token.
 *
 * Never throws: a throwing `jwt` callback makes Auth.js delete the session
 * cookie. On failure the token is returned as-is while it is still valid (the
 * next call tries again), and marked with `error` once it has really expired.
 */
async function refreshIdToken(token: JWT, expiresAt: number): Promise<JWT> {
  try {
    if (!token.refreshToken) throw new Error("no refresh token on the session")

    const response = await fetch(GOOGLE_TOKEN_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({
        client_id: process.env.AUTH_GOOGLE_ID ?? "",
        client_secret: process.env.AUTH_GOOGLE_SECRET ?? "",
        grant_type: "refresh_token",
        refresh_token: token.refreshToken,
      }),
      cache: "no-store",
    })
    const data: { id_token?: string; refresh_token?: string; error?: string } =
      await response.json()
    if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`)
    if (!data.id_token) throw new Error("response has no id_token")

    token.idToken = data.id_token
    token.idTokenExpiresAt =
      idTokenExpiry(data.id_token) ?? Math.floor(Date.now() / 1000) + 3600
    // Google normally keeps the same refresh token; store a new one if it rotates.
    if (data.refresh_token) token.refreshToken = data.refresh_token
    delete token.error
  } catch (e) {
    // Log the reason only — never the tokens themselves.
    console.error("[auth] Google ID token refresh failed:", e instanceof Error ? e.message : e)
    if (Date.now() / 1000 >= expiresAt) token.error = "RefreshTokenError"
  }
  return token
}

/**
 * NextAuth v5 (Auth.js) configuration.
 *
 * Required environment variables:
 *   AUTH_SECRET         – `openssl rand -base64 32`
 *   AUTH_GOOGLE_ID      – Google OAuth client ID
 *   AUTH_GOOGLE_SECRET  – Google OAuth client secret
 *
 * The Google provider requests the default scope (openid email profile),
 * which returns the minimum identity fields: sub, email, name, picture.
 */
export const { handlers, signIn, signOut, auth } = NextAuth({
  // Required on Vercel — NextAuth derives the canonical URL from request headers
  // (x-forwarded-host / x-forwarded-proto) rather than guessing.  Without this
  // the OAuth callback URL can resolve to an internal Vercel hostname and trip
  // the DNS_HOSTNAME_RESOLVED_PRIVATE block.
  trustHost: true,
  providers: [
    Google({
      // Google ID tokens expire after one hour. Offline access makes Google
      // issue a refresh token so the ID token can be renewed without the user
      // signing in again; `consent` is what makes Google hand out a refresh
      // token on every sign-in, not only on the very first one.
      //
      // `select_account` always shows Google's account chooser. Without it
      // Google silently signs in with whichever account the browser is already
      // using, so a person with two Google accounts can never pick the other one.
      authorization: {
        params: { access_type: "offline", prompt: "consent select_account" },
      },
    }),
  ],
  callbacks: {
    // Persist the Google `sub` (used as Langfuse user_id) AND the raw Google
    // ID token (forwarded to the FastAPI backend as a Bearer token, where it
    // is verified against Google's public keys to authenticate the user).
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.id = profile.sub ?? undefined
      }
      // `account` is only present on the initial sign-in callback, so we
      // store the tokens persistently on the encrypted NextAuth JWT.
      if (account) {
        if (account.id_token) {
          token.idToken = account.id_token
          token.idTokenExpiresAt = idTokenExpiry(account.id_token) ?? account.expires_at
        }
        if (account.refresh_token) token.refreshToken = account.refresh_token
        delete token.error
        return token
      }

      // Every later call (session fetch, middleware): renew the ID token when
      // it has expired or is about to, so the backend never sees a stale one.
      if (!token.idToken) return token
      const expiresAt = token.idTokenExpiresAt ?? idTokenExpiry(token.idToken) ?? 0
      if (Date.now() / 1000 < expiresAt - ID_TOKEN_REFRESH_MARGIN_SECONDS) return token
      return refreshIdToken(token, expiresAt)
    },
    async session({ session, token }) {
      if (session.user && token.id) {
        session.user.id = token.id
      }
      if (token.error) {
        // The ID token is dead and could not be renewed: tell the client to
        // send the user through sign-in again instead of handing it out.
        session.error = token.error
      } else if (token.idToken) {
        session.idToken = token.idToken
      }
      return session
    },
  },
  pages: {
    signIn: "/login",
  },
})
