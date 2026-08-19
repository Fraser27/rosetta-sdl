import { useEffect, useState } from 'react'
import { api, type BlockedQuery, type MatchThresholds, type MatchThresholdValues } from '../api'

const THRESHOLD_FIELDS: {
  key: keyof MatchThresholdValues
  label: string
  hint: string
  max: number
  step: number
}[] = [
  {
    key: 'metric_match_min_score',
    label: 'Metric match minimum (governed gate)',
    hint: 'Lucene score a metric must beat for a question to get a governed answer. Raise for stricter governance; lower to catch looser phrasings.',
    max: 5,
    step: 0.05,
  },
  {
    key: 'fulltext_confidence_threshold',
    label: 'Full-text confidence (FT threshold)',
    hint: 'Below this Lucene score the vector fallback also runs and may override the keyword match. Above it, vectors are skipped entirely.',
    max: 5,
    step: 0.05,
  },
  {
    key: 'vector_min_score',
    label: 'Vector minimum (Vector min)',
    hint: 'Minimum cosine similarity to accept a vector match. Bounded 0–1, unlike the two Lucene scores above.',
    max: 1,
    step: 0.01,
  },
]

export default function Governance() {
  const [blocked, setBlocked] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<BlockedQuery[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [historyLimit, setHistoryLimit] = useState(10)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)
  const [thresholds, setThresholds] = useState<MatchThresholds | null>(null)
  const [draft, setDraft] = useState<MatchThresholdValues | null>(null)
  const [savingThresholds, setSavingThresholds] = useState(false)

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
    api.getMatchThresholds()
      .then((r) => {
        setThresholds(r)
        setDraft({
          metric_match_min_score: r.metric_match_min_score,
          fulltext_confidence_threshold: r.fulltext_confidence_threshold,
          vector_min_score: r.vector_min_score,
        })
      })
      .catch(() => setThresholds(null))
  }, [])

  const saveThresholds = async (values: MatchThresholdValues) => {
    setSavingThresholds(true)
    try {
      const r = await api.setMatchThresholds(values)
      setThresholds(r)
      setDraft(values)
      showToast('Match thresholds updated — applies to the next question, no restart needed')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSavingThresholds(false)
    }
  }

  const dirty =
    !!draft && !!thresholds && THRESHOLD_FIELDS.some((f) => draft[f.key] !== thresholds[f.key])

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

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-header">
          <h3>Metric Match Thresholds</h3>
          {thresholds && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => saveThresholds(thresholds.defaults)}
              disabled={savingThresholds}
            >
              Reset to defaults
            </button>
          )}
        </div>

        {draft === null || thresholds === null ? (
          <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Loading thresholds…</p>
        ) : (
          <>
            <p style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 16, maxWidth: 720 }}>
              These three are independent and sit on <strong>different scales</strong> — the first two
              are unbounded Lucene/BM25 scores, the third is cosine similarity from 0–1. Only the
              first decides governed vs ungoverned; the other two decide <em>which</em> metric wins.
              Test the effect on the <strong>Similarity Explorer</strong> page.
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              {THRESHOLD_FIELDS.map((f) => (
                <div key={f.key}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
                    <label style={{ fontSize: 13, fontWeight: 600 }}>{f.label}</label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input
                        type="number"
                        min={0}
                        max={f.max}
                        step={f.step}
                        value={draft[f.key]}
                        onChange={(e) =>
                          setDraft({ ...draft, [f.key]: Number(e.target.value) })
                        }
                        style={{ width: 84, textAlign: 'right' }}
                      />
                      {draft[f.key] !== thresholds.defaults[f.key] && (
                        <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                          default {thresholds.defaults[f.key]}
                        </span>
                      )}
                    </div>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={f.max}
                    step={f.step}
                    value={draft[f.key]}
                    onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                    style={{ width: '100%', marginTop: 6 }}
                  />
                  <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: '4px 0 0' }}>{f.hint}</p>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 16 }}>
              <button
                className="btn btn-primary"
                onClick={() => saveThresholds(draft)}
                disabled={savingThresholds || !dirty}
              >
                {savingThresholds ? 'Saving…' : dirty ? 'Apply thresholds' : 'No changes'}
              </button>
              {dirty && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setDraft({
                    metric_match_min_score: thresholds.metric_match_min_score,
                    fulltext_confidence_threshold: thresholds.fulltext_confidence_threshold,
                    vector_min_score: thresholds.vector_min_score,
                  })}
                >
                  Discard
                </button>
              )}
            </div>

            <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 12 }}>
              Persisted in the graph — survives a restart. Applied in memory immediately, so the very
              next question uses the new values.
            </p>
          </>
        )}
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
