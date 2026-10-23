import { useState, type ReactNode } from 'react'
import { ShieldCheck } from 'lucide-react'
import type { Candidate } from '@/shared/model/types'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { BulletSection, ProfileCard, ProfileRows } from './fact-section'
import type { AadhaarOcr, CandidateOcr, PassportOcr } from './ocr-types'
import { AadhaarReading, PassportReading } from './ocr-sections'
import { primaryDocument } from '../ocr-view'
import type { ProfileModel } from '../profile-view'

export type { CandidateOcr, ResumeOcr } from './ocr-types'

/**
 * A tab's worth of cards. The page reads `data-profile-tab` from the DOM to build the tab bar,
 * so a group whose cards all had nothing to show (an empty element) gets no tab and takes no space.
 */
function TabGroup({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div
      id={`profile-${id}`}
      data-profile-tab={label}
      className="flex scroll-mt-40 flex-col gap-6 empty:hidden"
    >
      {children}
    </div>
  )
}

/**
 * Everything known about the candidate, grouped under the heading it belongs to. The saved record
 * comes first; the stored Veris reading fills in what the form never asked for.
 */
export function CandidateSections({
  cand,
  model,
  ocr,
  canViewFullIdNumbers = false,
}: {
  cand: Candidate
  model: ProfileModel
  ocr: CandidateOcr
  /** Whether this viewer may see a full Aadhaar number; everyone else gets the masked form. */
  canViewFullIdNumbers?: boolean
}) {
  const passport = primaryDocument(ocr, 'passport')?.payload as PassportOcr | undefined
  const aadhaar = primaryDocument(ocr, 'aadhaar')?.payload as AadhaarOcr | undefined

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabGroup id="details" label="Details">
        {model.details.length > 0 && (
          <ProfileCard title="Candidate details">
            <ProfileRows rows={model.details} />
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="summary" label="Summary">
        {model.summary && (
          <ProfileCard title="Summary">
            <p className="whitespace-pre-line pt-5 text-[17px] leading-7 text-ink-700">{model.summary}</p>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="experience" label="Experience">
        {model.jobs.length > 0 && (
          <ProfileCard title="Experience" meta={model.experienceMeta || undefined}>
            <ol className="pt-6">
              {model.jobs.map((j, i) => (
                <li key={i} className="relative border-l-2 border-rule pb-8 pl-7 last:border-transparent last:pb-1">
                  <span className="absolute -left-[7px] top-2 h-3 w-3 rounded-full bg-ink-700 ring-4 ring-white" />
                  <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-1">
                    <div className="min-w-0">
                      <p className="break-words text-[17px] font-semibold leading-7 text-ink">{j.title || 'Role not stated'}</p>
                      {(j.company || j.location || j.overseas) && (
                        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[16px] leading-6 text-ink-600">
                          {[j.company, j.location].filter(Boolean).join(' · ')}
                          {j.overseas && <Badge tone="navy" className="rounded-full px-2.5 py-0.5 text-[14px]">Overseas</Badge>}
                        </p>
                      )}
                    </div>
                    {(j.period || j.duration) && (
                      <div className="text-[15px] leading-6 text-ink-600 tnum sm:text-right">
                        {j.period && <p className="font-medium text-ink-700">{j.period}</p>}
                        {j.duration && <p>{j.duration}</p>}
                      </div>
                    )}
                  </div>
                  {j.description && <p className="mt-3 whitespace-pre-line text-[16px] leading-7 text-ink-700">{j.description}</p>}
                </li>
              ))}
            </ol>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="skills" label="Skills">
        {model.skills.length > 0 && (
          <ProfileCard title="Skills">
            <ul className="flex flex-wrap gap-2.5 pt-5">
              {model.skills.map((s) => (
                <li key={s} className="max-w-full break-words rounded-full border border-rule bg-field/70 px-4 py-1.5 text-[16px] font-medium leading-6 text-ink-700">
                  {s}
                </li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="education" label="Education">
        {model.education.length > 0 && (
          <ProfileCard title="Education">
            <ul className="space-y-3 pt-5">
              {model.education.map((e, i) => (
                <li key={i} className="flex flex-wrap items-start justify-between gap-x-6 gap-y-1 rounded-xl border border-rule-soft bg-field/50 px-5 py-4">
                  <div className="min-w-0">
                    <p className="break-words text-[17px] font-semibold leading-6 text-ink">{e.degree || e.institution}</p>
                    {(e.degree ? [e.institution, e.grade] : [e.grade]).filter(Boolean).length > 0 && (
                      <p className="mt-0.5 break-words text-[16px] text-ink-600">{(e.degree ? [e.institution, e.grade] : [e.grade]).filter(Boolean).join(' · ')}</p>
                    )}
                  </div>
                  {e.period && <p className="text-[15px] font-medium leading-6 text-ink-600 tnum">{e.period}</p>}
                </li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="passport" label="Passport">
        {/* The stated details only earn a card of their own when no passport scan was read. */}
        {!passport && model.passport.length > 0 && (
          <ProfileCard title="Passport">
            <ProfileRows rows={model.passport} />
          </ProfileCard>
        )}
        <PassportReading passport={passport} />
      </TabGroup>

      <TabGroup id="aadhaar" label="Aadhaar">
        <AadhaarReading aadhaar={aadhaar} canReveal={canViewFullIdNumbers} />
      </TabGroup>

      <TabGroup id="achievements" label="Achievements">
        <BulletSection title="Achievements" items={model.achievements} />
      </TabGroup>

      <TabGroup id="certifications" label="Certifications">
        <BulletSection title="Certifications" items={model.certifications} />
      </TabGroup>

      <TabGroup id="projects" label="Projects">
        {model.projects.length > 0 && (
          <ProfileCard title="Projects">
            <ul className="divide-y divide-rule-soft">
              {model.projects.map((p, i) => (
                <li key={i} className="py-4 last:pb-1">
                  <p className="break-words text-[17px] font-semibold text-ink-700">{p.name}</p>
                  {p.description && <p className="mt-1 whitespace-pre-line text-[16px] leading-7 text-ink-700">{p.description}</p>}
                </li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="additional" label="Additional">
        {model.additional.length > 0 && (
          <ProfileCard title="Additional information">
            <ProfileRows rows={model.additional} />
          </ProfileCard>
        )}
      </TabGroup>
    </div>
  )
}
