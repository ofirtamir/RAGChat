"use client"

import { useEffect, useState } from "react"
import { useSession } from "next-auth/react"
import { getMe, Me } from "@/lib/admin-api"

// One /api/me request per token, shared by every component that asks.
const cache = new Map<string, Promise<Me>>()

/** The signed-in user's access state (admin? approved? quota), or null while loading. */
export function useMe(): Me | null {
  const { data: session } = useSession()
  const token = session?.idToken
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => {
    if (!token) return
    let request = cache.get(token)
    if (!request) {
      request = getMe(token)
      cache.set(token, request)
      request.catch(() => cache.delete(token))
    }
    let active = true
    request.then(value => { if (active) setMe(value) }).catch(() => {})
    return () => { active = false }
  }, [token])

  return me
}
