import { DefaultSession } from "next-auth"

declare module "next-auth" {
  interface Session {
    user: {
      id: string
    } & DefaultSession["user"]
    /** Raw Google ID token, forwarded to the FastAPI backend as a Bearer token. */
    idToken?: string
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id?: string
    idToken?: string
  }
}
