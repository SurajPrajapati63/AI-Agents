import { useState } from 'react'
import { ArrowRight, Bot, CheckCircle2, Eye, EyeOff, LockKeyhole, Mail, Sparkles, UserPlus } from 'lucide-react'
import { login, signup, setAuthToken } from './services/api'
import './Auth.css'

function AuthPage({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ email: '', password: '', confirmPassword: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const isSignup = mode === 'signup'

  function updateField(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
    setError('')
  }

  function switchMode(nextMode) {
    setMode(nextMode)
    setError('')
    setForm((current) => ({ ...current, password: '', confirmPassword: '' }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')

    const email = form.email.trim()
    const password = form.password
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setError('Enter a valid email address.')
      return
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (isSignup && password !== form.confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      const payload = await (isSignup ? signup({ email, password }) : login({ email, password }))
      setAuthToken(payload.token)
      onAuthenticated(payload.user)
    } catch (requestError) {
      setError(requestError.message || 'Unable to continue. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-brand-panel">
        <div className="auth-brand">
          <div className="auth-brand-mark"><Sparkles size={20} /></div>
          <div><strong>Sourcewise</strong><span>Document intelligence</span></div>
        </div>
        <div className="auth-brand-content">
          <div className="auth-hero-icon"><Bot size={28} /></div>
          <p className="auth-eyebrow">A clearer way to work with your files</p>
          <h1>Turn documents into answers you can trust.</h1>
          <p className="auth-intro">Upload your source material and ask questions with confidence. Sourcewise keeps every answer grounded in the documents you provide.</p>
          <div className="auth-benefits">
            <div><CheckCircle2 size={17} /><span><strong>Source-grounded answers</strong>Responses stay tied to your uploaded content.</span></div>
            <div><CheckCircle2 size={17} /><span><strong>Private workspace</strong>Your account keeps your work organized and accessible.</span></div>
            <div><CheckCircle2 size={17} /><span><strong>Fast document search</strong>Find what matters across PDFs and text files.</span></div>
          </div>
        </div>
        <div className="auth-brand-footer">Built for focused research and everyday document questions</div>
      </section>

      <section className="auth-form-panel">
        <div className="auth-form-card">
          <div className="auth-form-heading">
            <div className="auth-form-icon">{isSignup ? <UserPlus size={20} /> : <LockKeyhole size={20} />}</div>
            <div>
              <p className="auth-eyebrow">Welcome to Sourcewise</p>
              <h2>{isSignup ? 'Create your account' : 'Sign in to your workspace'}</h2>
              <p>{isSignup ? 'Start organizing your documents in a few seconds.' : 'Continue exploring your document knowledge base.'}</p>
            </div>
          </div>

          <div className="auth-mode-switch" role="tablist" aria-label="Authentication mode">
            <button type="button" className={!isSignup ? 'active' : ''} onClick={() => switchMode('login')} role="tab" aria-selected={!isSignup}>Sign in</button>
            <button type="button" className={isSignup ? 'active' : ''} onClick={() => switchMode('signup')} role="tab" aria-selected={isSignup}>Sign up</button>
          </div>

          <form className="auth-form" onSubmit={handleSubmit}>
            <label className="auth-field">
              <span><Mail size={15} /> Email address</span>
              <input name="email" type="email" value={form.email} onChange={updateField} autoComplete="email" placeholder="you@example.com" required />
            </label>

            <label className="auth-field">
              <span><LockKeyhole size={15} /> Password</span>
              <div className="auth-password-input">
                <input name="password" type={showPassword ? 'text' : 'password'} value={form.password} onChange={updateField} autoComplete={isSignup ? 'new-password' : 'current-password'} placeholder={isSignup ? 'At least 8 characters' : 'Your password'} required />
                <button type="button" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button>
              </div>
            </label>

            {isSignup && <label className="auth-field">
              <span><LockKeyhole size={15} /> Confirm password</span>
              <input name="confirmPassword" type="password" value={form.confirmPassword} onChange={updateField} autoComplete="new-password" placeholder="Repeat your password" required />
            </label>}

            {error && <div className="auth-error" role="alert">{error}</div>}
            <button className="auth-submit" type="submit" disabled={loading}>
              {loading ? <span className="auth-spinner" /> : <ArrowRight size={17} />}
              {loading ? 'Please wait' : isSignup ? 'Create account' : 'Sign in'}
            </button>
          </form>

          <p className="auth-fine-print">By continuing, you agree to use Sourcewise responsibly and protect your account credentials.</p>
        </div>
      </section>
    </main>
  )
}

export default AuthPage
