import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Check, Eye, EyeOff, FileText, Globe2, Layers2, Plane, ShieldCheck } from 'lucide-react'
import { ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { agency } from '@/app/config/agency'
import { Button, Input } from '@/shared/ui/primitives'
import '@/styles/login.css'

export default function LoginPage() {
  const navigate = useNavigate()
  const { signIn, authenticated, loadError, ready, retryStartup } = useStore()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
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
    setError('')
    setEmailError('')
    setPasswordError('')
    setBusy(true)
    try {
      await signIn(email.trim(), password)
      navigate('/', { replace: true })
    } catch (err) {
      const detail = err instanceof ApiError && err.status === 401 ? err.detail : null
      if (
        detail &&
        typeof detail === 'object' &&
        'field' in detail &&
        'message' in detail &&
        typeof detail.message === 'string' &&
        (detail.field === 'email' || detail.field === 'password')
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
    <main className="login-page">
      <section className="login-story" aria-label="Care India recruitment workspace">
        <div className="login-brand"><span className="login-brand-mark"><Layers2 size={24} /></span><div><strong>{agency.name}</strong><span>Overseas recruitment</span></div></div>
        <div className="login-story-copy"><span className="login-kicker">People. Possibilities. Progress.</span><h2>Great careers<br />start with a<br /><span>connection.</span></h2><p>A shared workspace for your team, your candidates and every step of the journey.</p></div>
        <div className="login-scene" aria-hidden="true">
          <div className="login-orbit login-orbit-one" /><div className="login-orbit login-orbit-two" />
          <div className="login-globe"><Globe2 strokeWidth={0.7} /></div>
          <div className="login-flight"><Plane size={25} /></div>
          <div className="login-flow-card login-flow-first"><span className="login-flow-icon"><FileText size={18} /></span><div><small>THE FIRST STEP</small><strong>Resume received</strong><span>One candidate. New possibilities.</span></div><Check size={16} /></div>
          <div className="login-flow-card login-flow-last"><span className="login-flow-icon"><Plane size={18} /></span><div><small>THE NEXT CHAPTER</small><strong>Ready for deployment</strong><span>Every detail, in one place.</span></div></div>
        </div>
        <div className="login-story-footer"><span>From the first call to the airport.</span><span className="login-story-dots" aria-hidden="true"><i /><i /><i /></span></div>
      </section>

      <section className="login-form-panel" aria-label="Sign in to your workspace">
        <div className="login-mobile-brand"><Layers2 size={22} /><span>{agency.name}</span></div>
        <form onSubmit={submit} className="login-form">
          <div className="login-welcome-icon"><ShieldCheck size={24} strokeWidth={1.7} /></div>
          <span className="login-form-eyebrow">YOUR RECRUITMENT WORKSPACE</span>
          <h1>Welcome back.</h1>
          <p className="login-form-description">Sign in to pick up where you left off.</p>

          {loadError && !error && (
            <div role="alert" className="mt-4 rounded-control border border-stamp-100 bg-stamp-50 px-3 py-2 text-xs text-stamp-700">
              <p>Your session could not be restored. {loadError}</p>
              <button
                type="button"
                disabled={!ready || busy}
                onClick={() => void retryStartup()}
                className="mt-1 font-medium underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
              >
                Try again
              </button>
            </div>
          )}

          <div className="mt-6 space-y-4">
            <div className="ui-field block">
              <label htmlFor="login-email" className="mb-1.5 block text-xs font-medium text-ink-600">
                Email
              </label>
              <Input
                id="login-email"
                name="email"
                type="email"
                autoComplete="username"
                required
                disabled={busy}
                aria-invalid={Boolean(emailError)}
                aria-describedby={emailError ? 'login-email-error' : undefined}
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value)
                  setEmailError('')
                  setPasswordError('')
                  setError('')
                }}
                placeholder="you@youragency.com"
                className={emailError ? 'border-stamp-600' : undefined}
              />
              {emailError && (
                <p id="login-email-error" role="alert" className="mt-1 text-micro text-stamp-600">
                  {emailError}
                </p>
              )}
            </div>
            <div className="ui-field block">
              <label htmlFor="login-password" className="mb-1.5 block text-xs font-medium text-ink-600">
                Password
              </label>
              <div className="relative">
                <Input
                  id="login-password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  disabled={busy}
                  aria-invalid={Boolean(passwordError)}
                  aria-describedby={passwordError ? 'login-password-error' : undefined}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value)
                    setPasswordError('')
                    setError('')
                  }}
                  placeholder="Enter your password"
                  className={`pr-11${passwordError ? ' border-stamp-600' : ''}`}
                />
                <button
                  type="button"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  aria-controls="login-password"
                  onClick={() => setShowPassword((visible) => !visible)}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-control text-ink-400 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-500"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden="true" />
                  )}
                </button>
              </div>
              {passwordError && (
                <p id="login-password-error" role="alert" className="mt-1 text-micro text-stamp-600">
                  {passwordError}
                </p>
              )}
            </div>
          </div>

          {error && (
            <p role="alert" className="mt-3 rounded-control border border-stamp-100 bg-stamp-50 px-3 py-2 text-xs text-stamp-700">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" className="login-submit mt-6 w-full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}{!busy && <ArrowRight size={17} />}
          </Button>

          <p className="login-help">Need access or a password reset?<br /><span>Contact your agency administrator.</span></p>
        </form>
        <p className="login-form-footer"><ShieldCheck size={14} /> A private workspace for your recruitment team.</p>
      </section>
    </main>
  )
}
