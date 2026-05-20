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
    // Persist the Google `sub` as `id` on the session for use as Langfuse user_id.
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.id = profile.sub
      }
      return token
    },
    async session({ session, token }) {
      if (session.user && token.id) {
        session.user.id = token.id as string
      }
      return session
    },
  },
  pages: {
    signIn: "/login",
  },
})
