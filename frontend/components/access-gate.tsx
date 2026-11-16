"use client"

import { signOut } from "next-auth/react"
import { Button } from "@/components/ui/button"
import type { Me } from "@/lib/admin-api"

/** Shown instead of the chat to a signed-in user who is not allowed in (yet). */
export function AccessGate({ me }: { me: Me }) {
  const blocked = me.reason === "blocked"
  return (
    <div dir="rtl" className="flex h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center">
      <h1 className="text-2xl font-bold">{blocked ? "הגישה נחסמה" : "הבקשה שלך התקבלה"}</h1>
      <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
        {blocked
          ? "הגישה של החשבון הזה למערכת נחסמה. לבירור, פנה למנהל המערכת."
          : "החשבון שלך ממתין לאישור מנהל המערכת. אחרי האישור, רענן את הדף ותוכל להתחיל לשאול."}
      </p>
      <p dir="ltr" className="text-xs text-muted-foreground">{me.email}</p>
      <div className="flex gap-2">
        {!blocked && <Button onClick={() => window.location.reload()}>בדוק שוב</Button>}
        <Button variant="outline" onClick={() => signOut({ callbackUrl: "/login" })}>התנתק</Button>
      </div>
    </div>
  )
}
