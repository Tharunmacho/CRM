import { useState } from 'react'
import { UserCheck } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { AgencySettings } from '@/shared/model/types'
import { formFromSettings } from '@/shared/lib/agency'
import { autoAssignToggle } from '@/features/candidates/assignment-labels'
import { Panel } from '@/shared/ui/display'
import { Switch } from '@/shared/ui/primitives'
import { PanelHead, failureText } from './user-ui'

/**
 * Users > Agency settings > Assignment (admins change it): whether new candidates are given to a free recruiter automatically.
 * Off by default, so a new candidate waits until an admin or a manager picks a recruiter. It is saved at once with the settings
 * the server already holds (the save endpoint takes the whole profile), and shown disabled on a server that does not report it yet.
 */
export function AutoAssignCard() {
  const store = useStore()
  const settings = store.settings
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toggle = autoAssignToggle(settings, store.me.role === 'admin')

  const change = async (on: boolean) => {
    if (busy || toggle.disabled) return
    setBusy(true); setError('')
    try {
      await api.put<AgencySettings>('/settings', { ...formFromSettings(settings), autoAssign: on })
      try { await store.refresh({ keepCurrent: true }) } catch { /* the live update or the next read catches up */ }
      store.notify(on ? 'New candidates will be assigned automatically.' : 'New candidates now wait for you to assign a recruiter.')
    } catch (err) { setError(failureText(err)) } finally { setBusy(false) }
  }

  return <Panel flush className="uv-panel">
    <PanelHead id="as-assign-title" title="Candidate assignment" description="Who gets a new candidate. A staff member who adds a candidate by hand always keeps it." />
    <div className="as-body" role="group" aria-labelledby="as-assign-title">
      <div className="uv-toggle">
        <Switch label={toggle.label} description={toggle.helper} checked={toggle.checked} disabled={busy || toggle.disabled} onChange={(on) => void change(on)} />
      </div>
      {toggle.note && <p className="uv-note" role="status"><UserCheck size={14} aria-hidden="true" /> {toggle.note}</p>}
      {error && <p role="alert" className="uv-error">{error}</p>}
    </div>
  </Panel>
}
