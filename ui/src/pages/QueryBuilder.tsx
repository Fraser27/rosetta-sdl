import { useEffect, useState } from 'react'
import { api, type Metric } from '../api'

export default function QueryBuilder() {
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string[]>([])
  const [dimensions, setDimensions] = useState('')
  const [limit, setLimit] = useState('100')
  const [compiledSql, setCompiledSql] = useState<string | null>(null)
  const [results, setResults] = useState<{ columns: string[]; rows: string[][] } | null>(null)
  const [compiling, setCompiling] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listMetrics()
      .then(setMetrics)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const simpleMetrics = metrics.filter((m) => m.type === 'simple')

  const toggleMetric = (id: string) => {
    setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id])
    setCompiledSql(null)
    setResults(null)
    setError(null)
  }

  const parseDimensions = () =>
    dimensions.split(',').map((s) => s.trim()).filter(Boolean)

  const handleCompile = async () => {
    setCompiling(true)
    setError(null)
    setResults(null)
    try {
      const res = await api.composeMetrics(
        selected,
        parseDimensions(),
        parseInt(limit) || 100,
      )
      setCompiledSql(res.sql)
    } catch (e: unknown) {
      setError((e as Error).message)
      setCompiledSql(null)
    } finally {
      setCompiling(false)
    }
  }

  const handleExecute = async () => {
    setExecuting(true)
    setError(null)
    try {
      const res = await api.executeComposed(
        selected,
        parseDimensions(),
        parseInt(limit) || 100,
      )
      setCompiledSql(res.sql)
      setResults(res.results as { columns: string[]; rows: string[][] })
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setExecuting(false)
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Query Builder</h2>
        <p>Compose multiple governed metrics into a single CTE query — no code required</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Left panel: metric selection + dimensions */}
        <div>
          <div className="card" style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 12 }}>1. Select Metrics</h4>
            {simpleMetrics.length === 0 ? (
              <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
                No simple metrics available. Create metrics in the Metrics page first.
              </p>
            ) : (
              <div className="qb-metric-list">
                {simpleMetrics.map((m) => (
                  <label
                    key={m.metric_id}
                    className={`qb-metric-item ${selected.includes(m.metric_id) ? 'selected' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(m.metric_id)}
                      onChange={() => toggleMetric(m.metric_id)}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span className="tag tag-blue">{m.metric_id}</span>
                        <strong>{m.name}</strong>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>
                        <code>{m.expression}</code>
                        {m.source_table && <span> from {m.source_table}</span>}
                      </div>
                      {m.definition && (
                        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>{m.definition}</div>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 12 }}>2. Shared Dimensions</h4>
            <p style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8 }}>
              Columns to GROUP BY within each metric CTE and JOIN ON across them.
            </p>
            <input
              value={dimensions}
              onChange={(e) => { setDimensions(e.target.value); setCompiledSql(null); setResults(null) }}
              placeholder="e.g. region, order_date"
              style={{ width: '100%' }}
            />
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 12 }}>3. Options</h4>
            <div className="form-group">
              <label>Limit</label>
              <input
                type="number"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                style={{ width: 120 }}
                min={1}
                max={1000}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10 }}>
            <button
              className="btn btn-primary"
              onClick={handleCompile}
              disabled={selected.length < 2 || compiling}
            >
              {compiling ? 'Compiling...' : 'Compile SQL'}
            </button>
            <button
              className="btn btn-primary"
              onClick={handleExecute}
              disabled={selected.length < 2 || executing}
              style={{ background: 'var(--green)' }}
            >
              {executing ? 'Executing...' : 'Compile & Execute'}
            </button>
          </div>

          {selected.length > 0 && selected.length < 2 && (
            <p style={{ fontSize: 12, color: 'var(--orange)', marginTop: 8 }}>
              Select at least 2 metrics to compose.
            </p>
          )}
        </div>

        {/* Right panel: SQL + results */}
        <div>
          {error && (
            <div className="card" style={{ marginBottom: 16, borderColor: 'var(--red)' }}>
              <p style={{ color: 'var(--red)', fontSize: 13 }}>{error}</p>
            </div>
          )}

          {compiledSql && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <h4>Compiled SQL</h4>
                <span className="tag tag-green">governed</span>
              </div>
              <pre className="code-block">{compiledSql}</pre>
            </div>
          )}

          {results && (
            <div className="card">
              <h4 style={{ marginBottom: 12 }}>
                Results
                <span style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 'normal', marginLeft: 8 }}>
                  {results.rows.length} rows
                </span>
              </h4>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      {results.columns.map((col) => (
                        <th key={col}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {results.rows.map((row, i) => (
                      <tr key={i}>
                        {row.map((val, j) => (
                          <td key={j}>{val}</td>
                        ))}
                      </tr>
                    ))}
                    {results.rows.length === 0 && (
                      <tr>
                        <td colSpan={results.columns.length} className="empty-state">
                          No results returned.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!compiledSql && !error && (
            <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text-dim)' }}>
              <p>Select 2+ metrics and click Compile to see the CTE query.</p>
              <p style={{ fontSize: 12, marginTop: 8 }}>
                Each metric becomes a WITH clause. They are joined on shared dimensions.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
