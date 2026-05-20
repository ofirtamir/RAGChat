import { ChatLayout } from "@/components/chat-layout"

// Disable Vercel's full-page cache so the auth middleware runs on every request.
export const dynamic = "force-dynamic"

export default function Home() {
  return <ChatLayout />
}
