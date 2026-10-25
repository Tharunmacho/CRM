import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Clock3, Globe2, Save, ShieldCheck, Undo2 } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { AgencySettings } from '@/shared/model/types'
import { activeCurrencies, agencyFormChanged, agencyFormErrors, currencyLabel, formFromSettings, serverFieldErrors, type AgencyForm } from '@/shared/lib/agency'
import { dateTimeLabel } from '@/shared/lib/format'
import { Panel } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { CurrencyCard } from './currency-card'
import { PanelHead } from './user-ui'
import '@/styles/agency-settings.css'

type Errors = Partial<Record<keyof AgencyForm, string>>

/** Users > Agency settings: identity, defaults for new records, the reply text, the fixed rules and the currency list. */
export function AgencySettingsTab() {
  const store = useStore()
  const settings = store.settings
  const editable = settings.editable && store.me.role === 'admin'
  const saved = useMemo(() => formFromSettings(settings), [settings])
  const [form, setForm] = useState<AgencyForm>(saved)
  const [errors, setErrors] = useState<Errors>({})
  const [banner, setBanner] = useState('')
  const [saving, setSaving] = useState(false)
  const baseline = useRef(saved)

  // A change made elsewhere (or by someone else, announced live) replaces the form unless this person is mid-edit.
  useEffect(() => {
    setForm((current) => (JSON.stringify(current) === JSON.stringify(baseline.current) ? saved : current))
    baseline.current = saved
  }, [saved])

  const changed = agencyFormChanged(form, settings)
  const choices = activeCurrencies(settings.currencies)
  const set = <K extends keyof AgencyForm>(key: K, value: AgencyForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
    setErrors((current) => ({ ...current, [key]: undefined }))
  }

  const save = async () => {
    if (saving || !editable) return
    const found = agencyFormErrors(form, settings.currencies)
    setErrors(found)
    if (Object.keys(found).length) { setBanner('Check the highlighted fields.'); return }
    setSaving(true); setBanner('')
    try {
      await api.put<AgencySettings>('/settings', {
        ...form, homeCallingCode: form.homeCallingCode.replace(/\s+/g, ''),
      })
      try { await store.refresh({ keepCurrent: true }) } catch { /* the live update or the next read catches up */ }
      store.notify('Agency settings saved')
    } catch (error) {
      const fields = error instanceof ApiError ? serverFieldErrors(error.detail) : {}
      setErrors(fields as Errors)
      setBanner(Object.keys(fields).length ? 'Check the highlighted fields.' : error instanceof Error ? error.message : 'We could not save the settings. Try again.')
    } finally { setSaving(false) }
  }
  const changedBy = settings.updatedBy ? store.user(settings.updatedBy)?.name : undefined

  const currencySelect = (key: 'defaultOrderCurrency' | 'defaultAdvanceCurrency') => (
    <Select value={form[key]} onChange={(e) => set(key, e.target.value)}>
      {!choices.some((c) => c.code === form[key]) && <option value={form[key]}>{form[key] || 'Choose a currency'}</option>}
      {choices.map((c) => <option key={c.code} value={c.code}>{currencyLabel(c)}</option>)}
    </Select>
  )

  return <div className="as">
    {!settings.editable && <p role="status" className="uv-warn as-notice"><AlertTriangle size={16} aria-hidden="true" /> Agency settings are not available yet. The database update that adds them has not been applied, so the values below are only the built-in defaults and cannot be changed.</p>}

    <div className="as-layout">
      <form className="as-form" noValidate onSubmit={(event) => { event.preventDefault(); void save() }} aria-label="Agency profile and defaults">
        <fieldset disabled={!editable || saving} className="as-fields">
          <Panel flush className="uv-panel">
            <PanelHead id="as-name-title" title="Agency name" description="How your agency is named across the CRM." />
            <div className="as-body as-grid" role="group" aria-labelledby="as-name-title">
              <Field label="Agency name" required error={errors.name} hint="Shown on the sign-in page, the sidebar and the browser tab.">
                <Input value={form.name} maxLength={120} autoComplete="organization" onChange={(e) => set('name', e.target.value)} />
              </Field>
              <Field label="Short name" error={errors.shortName} hint="Used in tight places such as the sidebar. Leave empty to use the full name.">
                <Input value={form.shortName} maxLength={60} onChange={(e) => set('shortName', e.target.value)} />
              </Field>
            </div>
          </Panel>

          <Panel flush className="uv-panel">
            <PanelHead id="as-defaults-title" title="Defaults for new records" description="Pre-filled for everyone. People can still change them on each order or candidate." />
            <div className="as-body as-grid" role="group" aria-labelledby="as-defaults-title">
              <Field label="Default order currency" required error={errors.defaultOrderCurrency} hint="Pre-selected when someone creates a new order.">
                {currencySelect('defaultOrderCurrency')}
              </Field>
              <Field label="Default advance currency" required error={errors.defaultAdvanceCurrency} hint="Pre-selected for the advance payment stage.">
                {currencySelect('defaultAdvanceCurrency')}
              </Field>
              <Field label="Home calling code" required error={errors.homeCallingCode} hint="Your country's phone code, for example +91. Numbers with and without it are treated as the same person when checking for duplicates.">
                <Input value={form.homeCallingCode} maxLength={6} inputMode="tel" onChange={(e) => set('homeCallingCode', e.target.value)} />
              </Field>
              <Field label="Default nationality" error={errors.defaultNationality} hint="Pre-fills the nationality on the Add candidate form. Leave empty to pre-fill nothing.">
                <Input value={form.defaultNationality} maxLength={80} onChange={(e) => set('defaultNationality', e.target.value)} />
              </Field>
            </div>
          </Panel>

          <Panel flush className="uv-panel">
            <PanelHead id="as-ack-title" title="Acknowledgement text" description="The starting text for automatic replies." />
            <div className="as-body" role="group" aria-labelledby="as-ack-title">
              <Field label="Default acknowledgement text" required error={errors.acknowledgementText} hint="Used when you turn on automatic replies for a mailbox or WhatsApp number. You still preview and confirm it for each one.">
                <Textarea rows={4} value={form.acknowledgementText} maxLength={2000} onChange={(e) => set('acknowledgementText', e.target.value)} />
              </Field>
            </div>
          </Panel>
        </fieldset>

        {banner && <p role="alert" className="uv-error">{banner}</p>}
        <div className="as-savebar">
          <p className="as-stamp">
            {changed ? <span className="as-unsaved">Unsaved changes</span>
              : settings.updatedAt ? <><ShieldCheck size={14} aria-hidden="true" />Last saved {dateTimeLabel(settings.updatedAt)}{changedBy ? ` by ${changedBy}` : ''}</> : null}
          </p>
          <div className="as-savebar-actions">
            {changed && <Button type="button" variant="ghost" icon={<Undo2 className="h-4 w-4" />} disabled={saving} onClick={() => { setForm(saved); setErrors({}); setBanner('') }}>Discard changes</Button>}
            <Button type="submit" variant="primary" icon={<Save className="h-4 w-4" />} disabled={!editable || saving || !changed}>{saving ? 'Saving…' : 'Save changes'}</Button>
          </div>
        </div>
      </form>

      <aside className="as-side" aria-label="Fixed rules">
        <Panel flush className="uv-panel">
          <PanelHead id="as-rules-title" title="Fixed rules" description="Set by the server and the placement process. Shown here so nobody looks for a switch." />
          <dl className="as-body as-facts" aria-labelledby="as-rules-title">
            <div>
              <dt><Globe2 size={16} aria-hidden="true" />Time zone</dt>
              <dd>{settings.timezone || '—'}</dd>
              <dd className="as-note">Decides what "today" means for deadlines, daily remarks and attendance. Changing it mid-day would shift every worker's day, so it is part of the server setup.</dd>
            </div>
            <div>
              <dt><Clock3 size={16} aria-hidden="true" />Task deadline</dt>
              <dd>{settings.slaHours ? `${settings.slaHours} hours` : '—'}</dd>
              <dd className="as-note">Every stage task is due {settings.slaHours || 48} hours after it is raised. This is fixed by the placement process.</dd>
            </div>
          </dl>
        </Panel>
      </aside>
    </div>

    <CurrencyCard editable={editable} defaultOrder={settings.defaultOrderCurrency} defaultAdvance={settings.defaultAdvanceCurrency} />
  </div>
}
