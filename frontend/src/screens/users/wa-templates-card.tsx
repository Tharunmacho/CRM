import { useEffect, useMemo, useState } from 'react'
import { MessageCircle, RotateCcw, Save } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { leadExtrasApi, loadWaTemplates } from '@/features/leads/lead-extras-api'
import { WA_BODY_MAX, WA_PLACEHOLDERS, WA_TITLE_MAX, bodyRoom, fillTemplate, unknownPlaceholders, type WaTemplate } from '@/features/leads/wa-template-model'
import { PanelHead, failureText } from './user-ui'
import '@/styles/lead-extras.css'

/**
 * Users > Agency settings > Lead sheet WhatsApp templates (admins only): the three messages the green WhatsApp button offers on the
 * Lead sheet. {name}, {position} and {company_name} are filled for each lead; staff can still edit the text before sending.
 */
export function WaTemplatesCard({ editable }: { editable: boolean }) {
  const store = useStore()
  const isAdmin = store.me.role === 'admin'
  const [saved, setSaved] = useState<WaTemplate[] | null>(null)
  const [draft, setDraft] = useState<Record<string, { title: string; body: string }>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!isAdmin) return
    let alive = true
    leadExtrasApi.waTemplates().then((body) => {
      if (!alive) return
      setSaved(body.templates)
      setDraft(Object.fromEntries(body.templates.map((item) => [item.key, { title: item.title, body: item.body }])))
    }).catch((err) => { if (alive) setError(failureText(err)) })
    return () => { alive = false }
  }, [isAdmin])

  const problems = useMemo(() => Object.fromEntries((saved ?? []).map((item) => {
    const value = draft[item.key] ?? { title: item.title, body: item.body }
    const unknown = unknownPlaceholders(value.body)
    const issue = !value.title.trim() ? 'Give the template a title.' : !value.body.trim() ? 'The message cannot be empty.'
      : bodyRoom(value.body) < 0 ? `The message is ${-bodyRoom(value.body)} characters too long.` : unknown.length ? `Unknown placeholder ${unknown[0]}.` : ''
    return [item.key, issue]
  })), [saved, draft])
  if (!isAdmin) return null
  const changed = (saved ?? []).filter((item) => (draft[item.key]?.title ?? item.title) !== item.title || (draft[item.key]?.body ?? item.body) !== item.body)
  const blocked = Object.values(problems).some(Boolean)

  const apply = async (entries: { key: string; title?: string; body?: string; reset?: boolean }[], message: string) => {
    setBusy(true); setError(''); setNote('')
    try {
      const body = await leadExtrasApi.saveWaTemplates(entries)
      setSaved(body.templates)
      setDraft(Object.fromEntries(body.templates.map((item) => [item.key, { title: item.title, body: item.body }])))
      void loadWaTemplates(true)
      setNote(message)
    } catch (err) { setError(failureText(err)) } finally { setBusy(false) }
  }

  return <Panel flush className="uv-panel as-categories">
    <PanelHead id="as-wa-title" title="Lead sheet WhatsApp templates"
      description={<>The messages the green WhatsApp button offers on the Lead sheet. Use {WA_PLACEHOLDERS.map((token) => <span className="lwa-chip" key={token}>{token}</span>)} and each lead's own details are filled in. Recruiters can still edit the text before they send it.</>} />
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
    {!saved && !error && <p className="as-inline uv-note">Loading the templates…</p>}
    {saved && <div className="lwa-set">
      {saved.map((item) => {
        const value = draft[item.key] ?? { title: item.title, body: item.body }
        const preview = fillTemplate(value.body, { name: 'Asha Devi', position: 'Kitchen Helper', companyName: store.settings?.shortName || store.settings?.name || 'our agency' })
        const issue = problems[item.key]
        return <div className="lwa-set-item" key={item.key}>
          <header><strong><MessageCircle size={15} aria-hidden="true" style={{ verticalAlign: '-2px', marginRight: 6 }} />{item.defaultTitle ?? item.title}</strong>
            <Button variant="ghost" size="sm" icon={<RotateCcw className="h-3.5 w-3.5" />} disabled={!editable || busy || item.isDefault} onClick={() => void apply([{ key: item.key, reset: true }], `${item.defaultTitle ?? item.title} is back to the default wording.`)}>Use default</Button>
          </header>
          <label>Title<input type="text" maxLength={WA_TITLE_MAX} value={value.title} disabled={!editable || busy} onChange={(event) => setDraft((current) => ({ ...current, [item.key]: { ...value, title: event.target.value } }))} /></label>
          <label>Message ({Math.max(0, bodyRoom(value.body))} characters left of {WA_BODY_MAX})<textarea value={value.body} disabled={!editable || busy} onChange={(event) => setDraft((current) => ({ ...current, [item.key]: { ...value, body: event.target.value } }))} /></label>
          {issue ? <p role="alert" className="lwa-err">{issue}</p> : <p className="lwa-hint">Preview: {preview}</p>}
        </div>
      })}
      <div className="lwa-set-foot">
        <Button variant="primary" icon={<Save className="h-4 w-4" />} disabled={!editable || busy || blocked || changed.length === 0}
          onClick={() => void apply(changed.map((item) => ({ key: item.key, title: draft[item.key].title, body: draft[item.key].body })), 'WhatsApp templates saved. They apply to the next message anyone opens.')}>{busy ? 'Saving…' : 'Save templates'}</Button>
        {note && <p className="lwa-set-note" role="status">{note}</p>}
      </div>
    </div>}
  </Panel>
}
