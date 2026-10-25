import type { ReactNode } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/shared/lib/cn'

/**
 * Stepper: guided form steps drawn as a short route.
 *
 * Props
 *  - steps     { key, label, hint? }[]
 *  - current   string               key of the step being filled in
 *  - onSelect? (key) => void        lets people go back to steps they already finished (later steps stay locked)
 *  - label?    string               accessible name
 * Steps before `current` are done, `current` is lit, the rest are hollow. On phones only the current step name shows,
 * with "Step 2 of 4".
 */
export interface StepperStep { key: string; label: string; hint?: ReactNode }

export function Stepper({ steps, current, onSelect, label = 'Steps', className }: { steps: StepperStep[]; current: string; onSelect?: (key: string) => void; label?: string; className?: string }) {
  const at = Math.max(0, steps.findIndex((step) => step.key === current))
  return (
    <nav className={cn('v2-stepper', className)} aria-label={label}>
      <p className="v2-stepper-mobile" aria-hidden="true">Step {at + 1} of {steps.length}<span>{steps[at]?.label}</span></p>
      <ol className="v2-stepper-list">
        {steps.map((step, index) => {
          const state = index < at ? 'done' : index === at ? 'current' : 'todo'
          const inner = (
            <>
              <span className="v2-step-node" aria-hidden="true">{state === 'done' ? <Check size={12} strokeWidth={3.2} /> : index + 1}</span>
              <span className="v2-step-copy"><span className="v2-step-label">{step.label}</span>{step.hint && <span className="v2-step-hint">{step.hint}</span>}</span>
            </>
          )
          return (
            <li key={step.key} className="v2-step" data-state={state} aria-current={state === 'current' ? 'step' : undefined}>
              {onSelect && state === 'done'
                ? <button type="button" className="v2-step-hit" onClick={() => onSelect(step.key)}>{inner}</button>
                : <span className="v2-step-hit">{inner}</span>}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
