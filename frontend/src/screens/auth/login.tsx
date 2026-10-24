import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Eye, EyeOff, Globe2, Layers2, LoaderCircle, Lock, Mail, Moon, Plane, Sun, Timer, Workflow } from 'lucide-react'
import { ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { useTheme } from '@/shared/lib/theme'
import { STAGES } from '@/shared/workflow/stages'
import { agency } from '@/app/config/agency'
import '@/styles/login.css'

const REMEMBER_KEY = 'crm-remember-email'
const remembered = () => { try { return window.localStorage.getItem(REMEMBER_KEY) ?? '' } catch { return '' } }

/** Gulf destinations drawn from /public/flags. */
const DESTINATIONS = [
  { code: 'ae', name: 'UAE' }, { code: 'sa', name: 'Saudi Arabia' }, { code: 'qa', name: 'Qatar' },
  { code: 'kw', name: 'Kuwait' }, { code: 'om', name: 'Oman' }, { code: 'bh', name: 'Bahrain' },
]

/** The route is drawn in a 640 x 230 box; the eleven stage nodes are spread along it by arc length. */
const ROUTE = 'M 124 182 C 178 182 184 96 244 104 S 344 184 406 144 S 526 58 606 72'
const NODE_SPAN = 0.9
const HOLD_MS = 1650
const prefersReducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

type Point = { x: number; y: number }

/**
 * Decorative placement route: a candidate token rides the eleven real workflow stages from India to the Gulf.
 * Static copy only, no data; paused (fully drawn) for people who prefer reduced motion.
 */
function PlacementJourney() {
  const total = STAGES.length
  const reduced = useMemo(prefersReducedMotion, [])
  const [step, setStep] = useState(reduced ? total : 0)
  const [nodes, setNodes] = useState<Point[]>([])
  const routeRef = useRef<SVGPathElement>(null)
  const doneRef = useRef<SVGPathElement>(null)
  const planeRef = useRef<SVGGElement>(null)
  const lengthRef = useRef(0)
  const frameRef = useRef(0)
  const atRef = useRef(0)

  useLayoutEffect(() => {
    const path = routeRef.current
    if (!path) return
    const length = path.getTotalLength()
    lengthRef.current = length
    setNodes(Array.from({ length: total }, (_, i) => path.getPointAtLength((i / (total - 1)) * NODE_SPAN * length)))
    if (doneRef.current) { doneRef.current.style.strokeDasharray = `${length}`; doneRef.current.style.strokeDashoffset = `${length}` }
  }, [total])

  useEffect(() => {
    if (reduced) return undefined
    const timer = window.setTimeout(() => setStep((current) => (current >= total + 1 ? 0 : current + 1)), step === 0 ? HOLD_MS * 0.7 : HOLD_MS)
    return () => window.clearTimeout(timer)
  }, [step, total, reduced])

  const target = Math.min(step, total) >= total ? 1 : (Math.min(step, total - 1) / (total - 1)) * NODE_SPAN
  useEffect(() => {
    const path = routeRef.current
    const done = doneRef.current
    const plane = planeRef.current
    const length = lengthRef.current
    if (!path || !done || !plane || !length) return undefined
    const place = (fraction: number) => {
      const at = Math.max(0, Math.min(1, fraction)) * length
      const here = path.getPointAtLength(at)
      const ahead = path.getPointAtLength(Math.min(length, at + 1.5))
      const angle = Math.atan2(ahead.y - here.y, ahead.x - here.x) * (180 / Math.PI)
      plane.setAttribute('transform', `translate(${here.x} ${here.y}) rotate(${angle})`)
      done.style.strokeDashoffset = `${length - at}`
    }
    cancelAnimationFrame(frameRef.current)
    if (reduced || target === 0) { atRef.current = target; place(target); return undefined }
    const from = atRef.current
    const start = performance.now()
    const run = (now: number) => {
      const t = Math.min(1, (now - start) / 1150)
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
      const value = from + (target - from) * eased
      atRef.current = value
      place(value)
      if (t < 1) frameRef.current = requestAnimationFrame(run)
    }
    frameRef.current = requestAnimationFrame(run)
    return () => cancelAnimationFrame(frameRef.current)
  }, [target, reduced, nodes])

  const arrived = step >= total
  const current = arrived ? null : STAGES[Math.min(step, total - 1)]
  const shown = Math.min(step + 1, total)

  return (
    <div className="lg-journey" aria-hidden="true">
      <div className="lg-j-head">
        <span className="lg-j-tag"><Workflow size={14} />How a placement moves</span>
        <span className="lg-j-count">{arrived ? 'Deployed' : `Stage ${shown} of ${total}`}</span>
      </div>

      <div className="lg-j-map">
        <svg viewBox="0 0 640 230" className="lg-j-svg" focusable="false">
          <path d={ROUTE} className="lg-route-bed" />
          <path ref={routeRef} d={ROUTE} className="lg-route" />
          <path ref={doneRef} d={ROUTE} className="lg-route-done" />
          {nodes.map((point, i) => {
            const state = arrived || i < step ? 'done' : i === step ? 'now' : 'todo'
            return (
              <g key={STAGES[i].key} className={`lg-node is-${state}`} transform={`translate(${point.x} ${point.y})`}>
                {state === 'now' && <circle r="15" className="lg-node-ping" />}
                <circle r="10" className="lg-node-dot" />
                <text y="3.4" textAnchor="middle" className="lg-node-no">{i + 1}</text>
              </g>
            )
          })}
          <g className={`lg-end${arrived ? ' is-on' : ''}`} transform="translate(606 72)">
            <circle r="22" className="lg-end-ring" />
            <circle r="7" className="lg-end-dot" />
          </g>
          <g ref={planeRef} className="lg-plane">
            <path d="M 15 0 L -9 -9 L -5 0 L -9 9 Z" />
          </g>
        </svg>
        <span className="lg-pin lg-pin-start"><img src="/flags/in.svg" alt="" width={18} height={13} />India</span>
        <span className="lg-pin lg-pin-end">
          <span className="lg-stack">{DESTINATIONS.map((d, i) => <img key={d.code} src={`/flags/${d.code}.svg`} alt="" width={22} height={22} style={{ zIndex: DESTINATIONS.length - i }} />)}</span>
          Gulf
        </span>
      </div>

      <div className="lg-j-now">
        <span className="lg-j-token"><Plane size={16} /></span>
        <div className="lg-j-now-text">
          <small>{arrived ? 'Placement complete' : 'Sample candidate'}</small>
          <strong key={arrived ? 'done' : current?.key} className="lg-j-title">{arrived ? 'First day abroad' : current?.title}</strong>
        </div>
        <ol className="lg-tracker">
          {STAGES.map((stage, i) => <li key={stage.key} className={arrived || i < step ? 'is-done' : i === step ? 'is-now' : ''} />)}
        </ol>
      </div>
    </div>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const { theme, setTheme } = useTheme()
  const { signIn, authenticated, loadError, ready, retryStartup } = useStore()
  const [email, setEmail] = useState(remembered)
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(() => remembered() !== '')
  const [showPassword, setShowPassword] = useState(false)
  const [help, setHelp] = useState(false)
  const [emailError, setEmailError] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Already signed in — a refresh lands here only if the session is still good.
  useEffect(() => {
    if (authenticated) navigate('/', { replace: true })
  }, [authenticated, navigate])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setError(''); setEmailError(''); setPasswordError('')
    setBusy(true)
    try {
      await signIn(email.trim(), password)
      try { if (remember) window.localStorage.setItem(REMEMBER_KEY, email.trim()); else window.localStorage.removeItem(REMEMBER_KEY) } catch { /* optional */ }
      navigate('/', { replace: true })
    } catch (err) {
      const detail = err instanceof ApiError && err.status === 401 ? err.detail : null
      if (
        detail && typeof detail === 'object' && 'field' in detail && 'message' in detail && typeof detail.message === 'string'
        && (detail.field === 'email' || detail.field === 'password')
      ) {
        if (detail.field === 'email') setEmailError(detail.message)
        else setPasswordError(detail.message)
      } else {
        setError(err instanceof Error ? err.message || 'Could not sign in.' : 'Could not sign in.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="lg-page">
      <section className="lg-story" aria-label={`${agency.name} placement desk`}>
        <span className="lg-orbit lg-orbit-a" aria-hidden="true" />
        <span className="lg-orbit lg-orbit-b" aria-hidden="true" />

        <p className="lg-kicker"><span className="lg-kicker-dot" aria-hidden="true" />Gulf placement desk</p>

        <div className="lg-story-copy">
          <h1>From first call to <em>first day abroad.</em></h1>
          <p>Prescreening, client interviews, medical, visa and the air ticket: every candidate and every Gulf client on one tracked route.</p>
        </div>

        <PlacementJourney />
        <ol className="lg-sr">{STAGES.map((stage) => <li key={stage.key}>{stage.title}</li>)}</ol>

        <ul className="lg-values">
          <li><Workflow size={16} aria-hidden="true" />Eleven tracked stages</li>
          <li><Timer size={16} aria-hidden="true" />48-hour task deadline</li>
          <li><Globe2 size={16} aria-hidden="true" />Six Gulf destinations</li>
        </ul>
      </section>

      <section className="lg-access" aria-label="Sign in to your workspace">
        <div className="lg-brand-row">
          <span className="lg-brand"><span className="lg-brand-mark"><Layers2 size={22} strokeWidth={2.4} /></span><span><strong>{agency.name}</strong><small>Recruitment workspace</small></span></span>
          <button type="button" className="lg-theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} title={theme === 'dark' ? 'Light theme' : 'Dark theme'}>
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>

        <form className="lg-card" onSubmit={submit} noValidate>
          <header className="lg-heading">
            <h2>Sign in</h2>
            <p>Welcome back. Pick up your candidates, orders and tasks where you left them.</p>
          </header>

          {loadError && !error && (
            <div role="alert" className="lg-error">
              <AlertCircle size={16} aria-hidden="true" />
              <p>We could not sign you back in. {loadError} <button type="button" disabled={!ready || busy} onClick={() => void retryStartup()}>Try again</button></p>
            </div>
          )}

          <div className="lg-group">
            <label htmlFor="login-email" className="lg-label">Email address</label>
            <div className={`lg-field${emailError ? ' has-error' : ''}`}>
              <Mail size={17} className="lg-field-icon" aria-hidden="true" />
              <input id="login-email" name="email" type="email" inputMode="email" autoComplete="email" autoCapitalize="none" autoCorrect="off" spellCheck={false} required disabled={busy} value={email} placeholder="name@company.com"
                aria-invalid={Boolean(emailError)} aria-describedby={emailError ? 'login-email-error' : undefined}
                onChange={(e) => { setEmail(e.target.value); setEmailError(''); setPasswordError(''); setError('') }} />
            </div>
            {emailError && <p id="login-email-error" role="alert" className="lg-field-error">{emailError}</p>}
          </div>

          <div className="lg-group">
            <label htmlFor="login-password" className="lg-label">Password</label>
            <div className={`lg-field${passwordError ? ' has-error' : ''}`}>
              <Lock size={17} className="lg-field-icon" aria-hidden="true" />
              <input id="login-password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" autoCapitalize="none" autoCorrect="off" spellCheck={false} required disabled={busy} value={password} placeholder="Enter your password"
                aria-invalid={Boolean(passwordError)} aria-describedby={passwordError ? 'login-password-error' : undefined}
                onChange={(e) => { setPassword(e.target.value); setPasswordError(''); setError('') }} />
              <button type="button" className="lg-reveal" aria-label={showPassword ? 'Hide password' : 'Show password'} title={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} aria-controls="login-password" onClick={() => setShowPassword((visible) => !visible)}>
                {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
            </div>
            {passwordError && <p id="login-password-error" role="alert" className="lg-field-error">{passwordError}</p>}
          </div>

          <div className="lg-options">
            <label className="lg-remember"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /><span>Remember me</span></label>
            <button type="button" className="lg-link" aria-expanded={help} onClick={() => setHelp((value) => !value)}>Forgot password?</button>
          </div>
          {help && <p className="lg-help">Password resets are handled by your agency administrator. Ask them to reset it from User Management, then sign in with the temporary password.</p>}

          {error && (
            <p role="alert" className="lg-error"><AlertCircle size={16} aria-hidden="true" />{error}</p>
          )}

          <button type="submit" className="lg-submit" disabled={busy}>
            {busy ? <><LoaderCircle size={18} className="lg-spin" aria-hidden="true" />Signing in…</> : 'Sign in'}
          </button>
        </form>

        <p className="lg-foot">Need an account? <strong>Contact your administrator</strong></p>
      </section>
    </main>
  )
}
