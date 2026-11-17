import { memo } from 'react'
import { Check } from 'lucide-react'
import type { Step } from '../import-view'

/** The four steps at the top: finished ones are ticked, the current one is highlighted. */
export const ImportStepper = memo(function ImportStepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="ic-steps" aria-label="Import steps">
      {steps.map((step, index) => (
        <li key={step.key} className={`ic-step is-${step.state}`} aria-current={step.state === 'current' ? 'step' : undefined}>
          <span className="ic-step-dot" aria-hidden="true">{step.state === 'done' ? <Check strokeWidth={3} /> : index + 1}</span>
          <span className="ic-step-label">
            {step.label}
            {step.state === 'done' && <span className="sr-only"> (done)</span>}
          </span>
        </li>
      ))}
    </ol>
  )
})
