import { login, AUTH_ENABLED } from '../auth'

export default function Login() {
  if (!AUTH_ENABLED) return null

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '100vh',
      background: 'var(--bg)',
    }}>
      <div style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: 48,
        textAlign: 'center',
        maxWidth: 400,
        boxShadow: 'var(--shadow)',
      }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, color: 'var(--accent)', marginBottom: 8 }}>
          Rosetta SDL
        </h1>
        <p style={{ color: 'var(--text-dim)', fontSize: 14, marginBottom: 32 }}>
          Translate business language into data insights
        </p>
        <button className="btn btn-primary" onClick={login} style={{ width: '100%', padding: '12px 24px', fontSize: 15 }}>
          Sign in with Cognito
        </button>
        <p style={{ color: 'var(--text-dim)', fontSize: 12, marginTop: 16 }}>
          Authenticated via Amazon Cognito
        </p>
      </div>
    </div>
  )
}
