"use client"

import { signIn } from "next-auth/react"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { Button } from "@/components/ui/button"

function LoginContent() {
  const params = useSearchParams()
  const callbackUrl = params.get("callbackUrl") || "/"

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-sky-50 to-cyan-50 dark:from-slate-950 dark:to-slate-900" dir="rtl">
      <div className="w-full max-w-sm mx-4 bg-card border rounded-2xl shadow-xl p-8 flex flex-col items-center gap-6">
        <div className="flex gap-2">
          {[0,1,2,3,4].map(i => (
            <span
              key={i}
              className="w-3 h-3 rounded-full bg-sky-400 animate-thinking"
              style={{ animationDelay: `${i * 0.16}s` }}
            />
          ))}
        </div>

        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold bg-gradient-to-l from-sky-500 to-cyan-400 bg-clip-text text-transparent">
            שמאות AI
          </h1>
          <p className="text-sm text-muted-foreground leading-relaxed">
            מערכת חיפוש חכמה במסמכי שמאות,
            <br />חוות דעת ודוחות הערכה
          </p>
        </div>

        <Button
          onClick={() => signIn("google", { callbackUrl })}
          variant="outline"
          size="lg"
          className="w-full gap-3 h-12 bg-card hover:bg-accent"
        >
          <GoogleLogo />
          <span>התחבר עם חשבון Google</span>
        </Button>

        <p className="text-[11px] text-muted-foreground/70 text-center leading-relaxed">
          רק שמאים מורשים יכולים להתחבר.
          <br />ההתחברות שומרת רק שם, אימייל ותמונת פרופיל.
        </p>
      </div>
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
