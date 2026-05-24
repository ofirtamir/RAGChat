import NextAuth from "next-auth"
import Google from "next-auth/providers/google"

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
  providers: [Google],
  callbacks: {
    // Persist the Google `sub` (used as Langfuse user_id) AND the raw Google
    // ID token (forwarded to the FastAPI backend as a Bearer token, where it
    // is verified against Google's public keys to authenticate the user).
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.id = profile.sub
      }
      // `account` is only present on the initial sign-in callback, so we
      // store the id_token persistently on the encrypted NextAuth JWT.
      if (account?.id_token) {
        token.idToken = account.id_token
      }
      return token
    },
    async session({ session, token }) {
      if (session.user && token.id) {
        session.user.id = token.id as string
      }
      if (token.idToken) {
        session.idToken = token.idToken as string
      }
      return session
    },
  },
  pages: {
    signIn: "/login",
  },
})
