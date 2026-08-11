import { useEffect, useState } from 'react'
import { api, type BlockedQuery } from '../api'

export default function Governance() {
  const [blocked, setBlocked] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<BlockedQuery[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [historyLimit, setHistoryLimit] = useState(10)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 4000)
  }

  const loadHistory = () =>
    api.blockedQueries()
      .then((r) => { setHistory(r.blocked_queries); setHistoryLimit(r.limit) })
      .catch((e: unknown) => showToast((e as Error).message, 'error'))

  useEffect(() => {
    api.getBlockUngoverned()
      .then((r) => setBlocked(r.block_ungoverned_queries))
      .catch(() => setBlocked(null))
  }, [])

  const toggle = async () => {
    if (blocked === null) return
    const next = !blocked
    setSaving(true)
    try {
      await api.setBlockUngoverned(next)
      setBlocked(next)
      showToast(next ? 'Ungoverned queries are now blocked' : 'Ungoverned queries are now allowed')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const openHistory = () => {
    setShowHistory(true)
    loadHistory()
  }

  return (
    <>
      <div className="page-header">
        <h2>Governance</h2>
        <p>Control whether questions without a matching governed metric may fall back to LLM-generated SQL</p>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Ungoverned Queries</h3>
          {blocked !== null && (
            <span className={`tag ${blocked ? 'tag-red' : 'tag-green'}`}>
              {blocked ? 'Blocked' : 'Allowed'}
            </span>
          )}
        </div>
        <div style={{ padding: '4px 0 0' }}>
          <p style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 12, maxWidth: 720 }}>
            Governed metrics compile deterministically and are never affected by this setting.
            When blocked, a question that matches no <strong>approved</strong> metric is refused
            instead of answered with LLM-generated SQL — for the web UI, the MCP tools, and the
            plan endpoint alike. Refused questions are recorded below, and make a good backlog of
            metrics worth governing.
          </p>

          {blocked === null ? (
            <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Loading status…</p>
          ) : (
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <button
                className={`btn ${blocked ? 'btn-primary' : 'btn-danger'}`}
                onClick={toggle}
                disabled={saving}
              >
                {saving
                  ? 'Saving…'
                  : blocked ? 'Unblock ungoverned queries' : 'Block ungoverned queries'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={openHistory}>
                Blocked query history →
              </button>
            </div>
          )}

          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 12 }}>
            Persisted in the graph — survives a restart. Direct SQL
            (<code>POST /query/sql</code>) is a separate escape hatch and stays governed by the
            SQL firewall, not this switch.
          </p>
        </div>
      </div>

      {showHistory && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-header">
            <h3>Blocked Queries</h3>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="tag tag-blue">last {historyLimit}</span>
              <button className="btn btn-ghost btn-sm" onClick={loadHistory}>Refresh</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setShowHistory(false)}>Hide</button>
            </div>
          </div>

          {history === null ? (
            <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Loading…</p>
          ) : history.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>
              No queries have been blocked yet.
            </p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>User</th>
                  <th>Question</th>
                  <th>Route</th>
                </tr>
              </thead>
              <tbody>
                {history.map((b) => (
                  <tr key={b.event_id}>
                    <td style={{ whiteSpace: 'nowrap', fontSize: 12, color: 'var(--text-dim)' }}>
                      {new Date(b.timestamp).toLocaleString()}
                    </td>
                    <td style={{ fontSize: 13 }}>{b.user}</td>
                    <td style={{ fontSize: 13 }}>{b.question}</td>
                    <td><span className="tag tag-purple">{b.route || '—'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
