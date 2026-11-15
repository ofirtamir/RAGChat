import type { Metadata } from "next"
import { AdminConsole } from "@/components/admin/admin-console"

// The auth middleware runs on every request; who counts as an admin is decided
// by the backend (ADMIN_EMAILS), which also guards every admin API call.
export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "ניהול המערכת",
  robots: { index: false, follow: false },
}

export default function AdminPage() {
  return <AdminConsole />
}
