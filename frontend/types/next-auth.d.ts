import { DefaultSession } from "next-auth"

/** Set when the Google ID token expired and could not be refreshed. */
type AuthTokenError = "RefreshTokenError"

declare module "next-auth" {
  interface Session {
    user: {
      id: string
    } & DefaultSession["user"]
    /** Raw Google ID token, forwarded to the FastAPI backend as a Bearer token. */
    idToken?: string
    /** When set, the session cannot call the backend: the user must sign in again. */
    error?: AuthTokenError
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id?: string
    idToken?: string
    /** `exp` of `idToken`, in seconds since the epoch. */
    idTokenExpiresAt?: number
    /** Google refresh token, used server-side only to renew `idToken`. */
    refreshToken?: string
    error?: AuthTokenError
  }
}
