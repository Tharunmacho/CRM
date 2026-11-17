import { memo } from 'react'
import {
  BookUser, Briefcase, Camera, CircleHelp, Contact, File, FileSignature, FileText, GraduationCap, HeartPulse, ReceiptText, ShieldCheck, Stamp, Star, Plane, Shield,
  type LucideIcon,
} from 'lucide-react'
import type { DocChip } from '../import-view'

const ICONS: Record<string, LucideIcon> = {
  resume: FileText, application_form: FileText, passport: BookUser, aadhaar: Contact, photo: Camera,
  education_certificate: GraduationCap, education: GraduationCap, experience_certificate: Briefcase, experience: Briefcase,
  signed_offer: FileSignature, medical: HeartPulse, pcc: ShieldCheck, visa: Stamp, visa_stamping: Stamp, air_ticket: Plane,
  undertaking_letter: FileSignature, emigration: Plane, insurance: Shield, invoice: ReceiptText, google_review: Star, advance_receipt: ReceiptText,
  other: File, unsorted: CircleHelp,
}

export const DocIcon = ({ field }: { field: string }) => {
  const Icon = ICONS[field] ?? File
  return <Icon aria-hidden="true" />
}

/** The documents a candidate brings, one coloured chip per field with the number of files and, for a cut file, its pages. */
export const DocChips = memo(function DocChips({ chips }: { chips: DocChip[] }) {
  if (chips.length === 0) return <p className="ic-muted">No documents in this folder.</p>
  return (
    <ul className="ic-chips" aria-label="Documents">
      {chips.map((chip, index) => (
        <li key={chip.key} className={`ic-chip ic-tone-${chip.tone}`} style={{ animationDelay: `${Math.min(index, 12) * 28}ms` }}
          title={chip.guessed ? 'The name says nothing about this document. It is read when you import.' : undefined}>
          <DocIcon field={chip.key} />
          <span>{chip.label}</span>
          {chip.count > 1 && <b>×{chip.count}</b>}
          {chip.pages && <small>{chip.pages}</small>}
        </li>
      ))}
    </ul>
  )
})
