"use client"

import { ThinkingStep } from "@/types/chat"

interface ThinkingIndicatorProps {
  steps: ThinkingStep[]
  currentNode?: string
}

/**
 * Shows only the CURRENT step in a single fixed line (no scrolling).
 * The line stays in place and the text swaps as each step completes.
 */
export function ThinkingIndicator({ steps }: ThinkingIndicatorProps) {
  // Pick the last step (= current) or show generic "thinking"
  const currentStep = steps.length > 0 ? steps[steps.length - 1] : null

  return (
    <div className="flex h-8 items-center gap-3">
      {/* Pulsing dot */}
      <span className="relative flex h-3 w-3 shrink-0">
        <span className="absolute inset-0 animate-ping rounded-full bg-foreground/30" />
        <span className="relative h-3 w-3 rounded-full bg-foreground" />
      </span>

      {/* Current step text – fades in on change */}
      <div className="flex min-w-0 items-center overflow-hidden">
        {currentStep ? (
          <div key={currentStep.node} className="flex min-w-0 items-baseline gap-1.5 animate-fadeIn">
            <span className="text-shimmer whitespace-nowrap text-[15px]">{currentStep.label}</span>
            {currentStep.detail && (
              <span className="truncate text-xs text-muted-foreground">· {currentStep.detail}</span>
            )}
          </div>
        ) : (
          <span className="text-shimmer text-[15px] animate-fadeIn">חושב…</span>
        )}
      </div>
    </div>
  )
}
