import type { ReactNode } from 'react'
import { Award, BookUser, Briefcase, FileText, FolderKanban, GraduationCap, Info, Sparkles, Trophy, UserRound, Wallet } from 'lucide-react'
import type { Candidate } from '@/shared/model/types'
import { dateLabel, money } from '@/shared/lib/format'
import { Badge } from '@/shared/ui/display'
import { BulletSection, FactGrid, ProfileCard, ProfileRows, type Fact } from './fact-section'
import type { AadhaarOcr, CandidateOcr, PassportOcr } from './ocr-types'
import { AadhaarReading, PassportReading } from './ocr-sections'
import { primaryDocument } from '../ocr-view'
import { SOURCE_LABEL, type ProfileModel } from '../profile-view'

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
      className="flex min-w-0 scroll-mt-32 flex-col gap-4 empty:hidden"
    >
      {children}
    </div>
  )
}

/**
 * The Add Candidate fields the Veris sections do not already show: overseas experience and expected
 * salary (work profile) and where the candidate came from (source). Read from the saved record.
 */
function workProfileFacts(cand: Candidate): Fact[] {
  const years = (value: number | null | undefined) => (value == null ? 'Not recorded' : `${value} ${value === 1 ? 'year' : 'years'}`)
  return [
    ['Gulf or overseas experience', years(cand.gulfExperience)],
    ['Expected salary', cand.expectedSalary != null ? money(cand.expectedSalary) : 'Not recorded'],
    ['Source', SOURCE_LABEL[cand.source] ?? cand.source],
    ['More about the source', cand.sourceDetail || 'Not recorded'],
    ['Added on', dateLabel(cand.addedAt)],
  ]
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
    <div className="contents">
      <TabGroup id="details" label="Details">
        {model.details.length > 0 && (
          <ProfileCard title="Candidate details" icon={<UserRound size={18} />} meta="Who they are and how to reach them">
            <ProfileRows rows={model.details} />
          </ProfileCard>
        )}
      </TabGroup>
      {/* A card of its own (not inside the Details group), so the page can place it beside the other cards. */}
      <ProfileCard title="Work profile and source" icon={<Wallet size={18} />} meta="Work abroad, the salary they want, and how they found us">
        <FactGrid facts={workProfileFacts(cand)} />
      </ProfileCard>

      <TabGroup id="summary" label="Summary">
        {model.summary && (
          <ProfileCard title="Summary" icon={<FileText size={18} />}>
            <p className="cp-prose">{model.summary}</p>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="experience" label="Experience">
        {model.jobs.length > 0 && (
          <ProfileCard title="Experience" icon={<Briefcase size={18} />} meta={model.experienceMeta || undefined}>
            <ol className="cp-timeline">
              {model.jobs.map((j, i) => (
                <li key={i}>
                  <div className="cp-item">
                    <div className="min-w-0">
                      <p className="cp-item-title">{j.title || 'Job title not given'}</p>
                      {(j.company || j.location || j.overseas) && (
                        <p className="cp-item-sub">
                          {[j.company, j.location].filter(Boolean).join(' · ')}
                          {j.overseas && <Badge tone="navy">Overseas</Badge>}
                        </p>
                      )}
                    </div>
                    {(j.period || j.duration) && (
                      <div className="cp-item-when tnum">
                        {j.period && <p className="cp-item-when-main">{j.period}</p>}
                        {j.duration && <p>{j.duration}</p>}
                      </div>
                    )}
                  </div>
                  {j.description && <p className="cp-note-row whitespace-pre-line">{j.description}</p>}
                </li>
              ))}
            </ol>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="skills" label="Skills">
        {model.skills.length > 0 && (
          <ProfileCard title="Skills" icon={<Sparkles size={18} />}>
            <ul className="cp-tags">
              {model.skills.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="education" label="Education">
        {model.education.length > 0 && (
          <ProfileCard title="Education" icon={<GraduationCap size={18} />}>
            <ul className="cp-stack">
              {model.education.map((e, i) => (
                <li key={i} className="cp-item">
                  <div className="min-w-0">
                    <p className="cp-item-title">{e.degree || e.institution}</p>
                    {(e.degree ? [e.institution, e.grade] : [e.grade]).filter(Boolean).length > 0 && (
                      <p className="cp-item-sub">{(e.degree ? [e.institution, e.grade] : [e.grade]).filter(Boolean).join(' · ')}</p>
                    )}
                  </div>
                  {e.period && <p className="cp-item-when tnum"><span className="cp-item-when-main">{e.period}</span></p>}
                </li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="passport" label="Passport">
        {/* The stated details only earn a card of their own when no passport scan was read. */}
        {!passport && model.passport.length > 0 && (
          <ProfileCard title="Passport" icon={<BookUser size={18} />}>
            <ProfileRows rows={model.passport} />
          </ProfileCard>
        )}
        <PassportReading passport={passport} />
      </TabGroup>

      <TabGroup id="aadhaar" label="Aadhaar">
        <AadhaarReading aadhaar={aadhaar} canReveal={canViewFullIdNumbers} />
      </TabGroup>

      <TabGroup id="achievements" label="Achievements">
        <BulletSection title="Achievements" icon={<Trophy size={18} />} items={model.achievements} />
      </TabGroup>

      <TabGroup id="certifications" label="Certifications">
        <BulletSection title="Certifications" icon={<Award size={18} />} items={model.certifications} />
      </TabGroup>

      <TabGroup id="projects" label="Projects">
        {model.projects.length > 0 && (
          <ProfileCard title="Projects" icon={<FolderKanban size={18} />}>
            <ul className="divide-y divide-rule-soft">
              {model.projects.map((p, i) => (
                <li key={i} className="py-3 first:pt-0 last:pb-0">
                  <p className="cp-item-title">{p.name}</p>
                  {p.description && <p className="cp-prose is-small">{p.description}</p>}
                </li>
              ))}
            </ul>
          </ProfileCard>
        )}
      </TabGroup>

      <TabGroup id="additional" label="Additional">
        {model.additional.length > 0 && (
          <ProfileCard title="Additional information" icon={<Info size={18} />}>
            <ProfileRows rows={model.additional} />
          </ProfileCard>
        )}
      </TabGroup>
    </div>
  )
}
