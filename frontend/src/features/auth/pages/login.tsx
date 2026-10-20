import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff } from 'lucide-react'
import { ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { agency } from '@/app/config/agency'
import { Button, Input } from '@/shared/ui/primitives'

export default function LoginPage() {
  const navigate = useNavigate()
  const { signIn, authenticated } = useStore()
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
    <div className="flex min-h-screen">
      {/* The left panel carries the one piece of identity this screen needs. */}
      <div className="hidden w-[46%] flex-col justify-between bg-navy-600 p-10 lg:flex">
        <div>
          <p className="text-lead font-semibold text-white">{agency.name}</p>
          <p className="mt-0.5 text-xs text-white/80">Overseas recruitment</p>
        </div>

        <div className="max-w-[34ch]">
          <p className="text-h2 font-semibold leading-snug text-white">
            Eleven stages, from the first call to the airport.
          </p>
          <p className="mt-3 text-sm leading-relaxed text-white/85">
            Every hand-off becomes a task with a 48-hour limit. Nothing sits unnoticed.
          </p>
        </div>

        <ol className="flex gap-1" aria-hidden>
          {Array.from({ length: 11 }, (_, i) => (
            <li
              key={i}
              className="h-1 flex-1 rounded-full"
              style={{
                backgroundColor: i < 7 ? '#FFFFFF' : 'rgba(255,255,255,0.22)',
              }}
            />
          ))}
        </ol>
      </div>

      <div className="flex flex-1 items-center justify-center bg-white px-6 py-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <h1 className="text-h1 font-semibold text-ink">Sign in</h1>
          <p className="mt-1 text-sm text-ink-500">Use your agency email address.</p>

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
                placeholder="you@careindiaoverseas.com"
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

          <Button type="submit" variant="primary" className="mt-5 w-full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>

          <p className="mt-6 border-t border-rule-soft pt-4 text-micro leading-relaxed text-ink-400">
            Your session is held in a secure cookie, so a refresh keeps you signed in. An admin
            can revoke it from User Management.
          </p>
        </form>
      </div>
    </div>
  )
}
