"use client"

import { ThinkingStep } from "@/types/chat"
import { cn } from "@/lib/utils"
import { Sparkles } from "lucide-react"

interface ThinkingIndicatorProps {
  steps: ThinkingStep[]
  currentNode?: string
}

/**
 * Shows only the CURRENT step in a single fixed line (no scrolling).
 * The line stays in place and the text swaps as each step completes.
 */
export function ThinkingIndicator({ steps, currentNode }: ThinkingIndicatorProps) {
  // Pick the last step (= current) or show generic "thinking"
  const currentStep = steps.length > 0 ? steps[steps.length - 1] : null

  return (
    <div className="flex items-center gap-2.5 py-2 h-10">
      {/* Animated sparkle */}
      <div className="relative flex items-center justify-center w-6 h-6 shrink-0">
        <div className="absolute inset-0 rounded-full bg-gradient-to-br from-sky-400/20 to-cyan-400/20 animate-pulse" />
        <Sparkles className="w-4 h-4 text-sky-500 animate-sparkle" />
      </div>

      {/* Current step text – fades/slides on change */}
      <div className="flex items-center gap-2 min-w-0 overflow-hidden">
        {currentStep ? (
          <div key={currentStep.node} className="flex items-center gap-1.5 animate-fadeIn">
            <span className="text-sm leading-none">{currentStep.icon}</span>
            <span className="text-[13px] font-medium text-foreground whitespace-nowrap">
              {currentStep.label}
            </span>
            {currentStep.detail && (
              <span className="text-[11px] text-muted-foreground truncate max-w-[200px]">
                — {currentStep.detail}
              </span>
            )}
          </div>
        ) : (
          <span className="text-[13px] font-medium text-foreground animate-fadeIn">
            חושב...
          </span>
        )}
      </div>
    </div>
  )
}
