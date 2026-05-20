import type { Metadata } from "next"
import { Rubik } from "next/font/google"
import "./globals.css"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthSessionProvider } from "@/components/session-provider"

const rubik = Rubik({
  subsets: ["latin", "hebrew"],
  variable: "--font-rubik",
})

export const metadata: Metadata = {
  metadataBase: new URL("https://rag-chat-smoky-alpha.vercel.app"),
  title: "שמאות AI",
  description: "מערכת חיפוש חכמה במסמכי שמאות, מבוססת בינה מלאכותית",
  openGraph: {
    title: "שמאות AI",
    description: "מערכת חיפוש חכמה במסמכי שמאות, מבוססת בינה מלאכותית",
    url: "https://rag-chat-smoky-alpha.vercel.app",
    siteName: "שמאות AI",
    locale: "he_IL",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "שמאות AI",
    description: "מערכת חיפוש חכמה במסמכי שמאות, מבוססת בינה מלאכותית",
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="he" dir="rtl" suppressHydrationWarning>
      <body className={`${rubik.variable} font-sans antialiased`}>
        <AuthSessionProvider>
          <ThemeProvider
            attribute="class"
            defaultTheme="light"
            enableSystem
            disableTransitionOnChange
          >
            {children}
          </ThemeProvider>
        </AuthSessionProvider>
      </body>
    </html>
  )
}
