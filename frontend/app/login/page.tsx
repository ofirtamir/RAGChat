"use client"

import { signIn } from "next-auth/react"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { Button } from "@/components/ui/button"

function LoginContent() {
  const params = useSearchParams()
  const callbackUrl = params.get("callbackUrl") || "/"
  const sessionExpired = params.get("reason") === "expired"

  return (
    <div className="flex min-h-dvh flex-col bg-background" dir="rtl">
      <header className="px-5 py-4 text-[17px] font-semibold tracking-tight">שמאות AI</header>

      <main className="flex flex-1 items-center justify-center px-5 pb-[12vh]">
        <div className="flex w-full max-w-[22rem] flex-col items-center gap-8">
          <div className="space-y-3 text-center">
            <h1 className="text-[28px] font-normal tracking-tight">ברוכים הבאים</h1>
            <p className="text-[15px] leading-relaxed text-muted-foreground">
              חיפוש חכם במסמכי שמאות, חוות דעת ודוחות הערכה
            </p>
          </div>

          {sessionExpired && (
            <p
              role="status"
              className="w-full rounded-lg border bg-muted px-4 py-3 text-center text-sm leading-relaxed"
            >
              פג תוקף ההתחברות. יש להתחבר שוב כדי להמשיך.
            </p>
          )}

          <Button
            onClick={() => signIn("google", { callbackUrl })}
            variant="outline"
            size="lg"
            className="h-[52px] w-full gap-3 text-[15px] font-normal"
          >
            <GoogleLogo />
            <span>המשך עם Google</span>
          </Button>

          <p className="text-center text-xs leading-relaxed text-muted-foreground">
            רק שמאים מורשים יכולים להתחבר.
            <br />ההתחברות שומרת רק שם, אימייל ותמונת פרופיל.
          </p>
        </div>
      </main>
    </div>
  )
}

function GoogleLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center">טוען…</div>}>
      <LoginContent />
    </Suspense>
  )
}
