import { useState } from 'react'
import { api, type SimilarityTestResult } from '../api'

export default function SimilarityExplorer() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<SimilarityTestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const runTest = async () => {
    if (!question.trim()) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const res = await api.similarityTest(question.trim())
      setResult(res)
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const resolutionLabel = (r: string) => {
    switch (r) {
      case 'fulltext': return { text: 'Full-Text Match', cls: 'tag-green' }
      case 'vector': return { text: 'Vector Match', cls: 'tag-purple' }
      case 'fulltext_weak': return { text: 'Weak Full-Text (below threshold)', cls: 'tag-orange' }
      case 'none': return { text: 'No Match — Ungoverned', cls: 'tag-red' }
      default: return { text: r, cls: 'tag-blue' }
    }
  }

  const scoreBar = (score: number, maxScore: number, color: string) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 180 }}>
      <div style={{ flex: 1, height: 6, background: 'var(--bg)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{
          height: '100%',
          width: `${Math.min((score / (maxScore || 1)) * 100, 100)}%`,
          background: color,
          borderRadius: 3,
        }} />
      </div>
      <span style={{ fontSize: 12, fontWeight: 600, minWidth: 50, textAlign: 'right' }}>
        {score.toFixed(3)}
      </span>
    </div>
  )

  return (
    <>
      <div className="page-header">
        <h2>Similarity Explorer</h2>
        <p>Test how user questions are matched to governed metrics via full-text and vector similarity search</p>
      </div>

      <div className="card">
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runTest()}
            placeholder='Try a question like "what is the price?" or "show me revenue"...'
            style={{
              flex: 1, padding: '10px 16px',
              background: 'var(--bg)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius)', color: 'var(--text)', fontSize: 14,
            }}
          />
          <button className="btn btn-primary" onClick={runTest} disabled={loading || !question.trim()}>
            {loading ? 'Testing...' : 'Test'}
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ marginTop: 16, borderColor: 'var(--red)' }}>
          <p style={{ color: 'var(--red)', fontSize: 14 }}>{error}</p>
        </div>
      )}

      {result && (
        <>
          {/* Resolution banner */}
          <div className="card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <span style={{ fontSize: 13, color: 'var(--text-dim)', marginRight: 8 }}>Resolution:</span>
                <span className={`tag ${resolutionLabel(result.resolution).cls}`}>
                  {resolutionLabel(result.resolution).text}
                </span>
                {result.selected_metric && (
                  <span style={{ marginLeft: 12, fontSize: 14, fontWeight: 600 }}>
                    {result.selected_metric}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', textAlign: 'right' }}>
                <div>FT threshold: {result.thresholds.fulltext_confidence}</div>
                <div>Vector min: {result.thresholds.vector_min_score}</div>
              </div>
            </div>
          </div>

          {/* Side-by-side results */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 16 }}>
            {/* Full-text results */}
            <div className="card">
              <div className="card-header">
                <h3>Full-Text Results</h3>
                <span className="tag tag-blue">{result.fulltext_results.length} hits</span>
              </div>
              {result.fulltext_results.length === 0 ? (
                <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>No full-text matches found</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {result.fulltext_results.map((hit, i) => {
                    const maxFt = result.fulltext_results[0]?.score || 1
                    const isSelected = result.resolution === 'fulltext' && i === 0
                    return (
                      <div
                        key={hit.metric_id}
                        style={{
                          padding: '10px 12px',
                          border: `1px solid ${isSelected ? 'var(--green)' : 'var(--border)'}`,
                          borderRadius: 'var(--radius)',
                          background: isSelected ? 'rgba(22, 163, 74, 0.05)' : 'transparent',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                          <span style={{ fontWeight: 600, fontSize: 14 }}>
                            {hit.name}
                            {isSelected && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--green)' }}>SELECTED</span>}
                          </span>
                        </div>
                        {scoreBar(hit.score, maxFt, 'var(--accent)')}
                        {hit.definition && (
                          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 6 }}>{hit.definition}</p>
                        )}
                        {hit.synonyms && hit.synonyms.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            {hit.synonyms.map((s) => (
                              <span key={s} className="tag tag-blue" style={{ marginRight: 4, fontSize: 11 }}>{s}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Vector results */}
            <div className="card">
              <div className="card-header">
                <h3>Vector Results</h3>
                <span className="tag tag-purple">{result.vector_results.length} hits</span>
              </div>
              {result.vector_results.length === 0 ? (
                <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>No vector matches found (embeddings may not be computed yet)</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {result.vector_results.map((hit, i) => {
                    const isSelected = result.resolution === 'vector' && i === 0
                    return (
                      <div
                        key={hit.metric_id}
                        style={{
                          padding: '10px 12px',
                          border: `1px solid ${isSelected ? 'var(--purple)' : 'var(--border)'}`,
                          borderRadius: 'var(--radius)',
                          background: isSelected ? 'rgba(124, 58, 237, 0.05)' : 'transparent',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                          <span style={{ fontWeight: 600, fontSize: 14 }}>
                            {hit.name}
                            {isSelected && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--purple)' }}>SELECTED</span>}
                          </span>
                        </div>
                        {scoreBar(hit.score, 1.0, 'var(--purple)')}
                        {hit.definition && (
                          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 6 }}>{hit.definition}</p>
                        )}
                        {hit.synonyms && hit.synonyms.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            {hit.synonyms.map((s) => (
                              <span key={s} className="tag tag-purple" style={{ marginRight: 4, fontSize: 11 }}>{s}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </>
  )
}
