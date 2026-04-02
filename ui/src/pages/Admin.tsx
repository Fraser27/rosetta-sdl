import { useState } from 'react'
import { api } from '../api'

export default function Admin() {
  const [results, setResults] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 4000)
  }

  const runAction = async (name: string, fn: () => Promise<Record<string, unknown>>) => {
    if (name === 'clear' && !confirm('This will delete ALL nodes and edges from the graph. Are you sure?')) return
    setLoading(name)
    setResults(null)
    try {
      const res = await fn()
      setResults(res)
      showToast(`${name} completed successfully`)
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setLoading(null)
    }
  }

  return (
    <>
      <div className="page-header">
        <h2>Admin</h2>
        <p>Manage the semantic layer graph: scan data sources, enrich metadata, clear graph</p>
      </div>

      <div className="admin-actions">
        <div className="admin-card">
          <h3>Scan Data Sources</h3>
          <p>Scan configured Glue databases and S3 Vector buckets. Populates the graph with tables, columns, documents, metrics, and join paths.</p>
          <button
            className="btn btn-primary"
            onClick={() => runAction('scan', api.scan)}
            disabled={loading !== null}
          >
            {loading === 'scan' ? 'Scanning...' : 'Run Scan'}
          </button>
        </div>

        <div className="admin-card">
          <h3>Enrich Metadata</h3>
          <p>Use LLM (Bedrock) to generate descriptions for tables and columns, extract concepts from documents, and create business term mappings.</p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-primary"
              onClick={() => runAction('enrich', () => api.enrich())}
              disabled={loading !== null}
            >
              {loading === 'enrich' ? 'Enriching...' : 'Enrich New'}
            </button>
            <button
              className="btn btn-ghost"
              onClick={() => runAction('enrich', () => api.enrich(true))}
              disabled={loading !== null}
              title="Re-enrich all tables and documents, even those with existing descriptions"
            >
              {loading === 'enrich' ? 'Enriching...' : 'Force Re-enrich All'}
            </button>
          </div>
        </div>

        <div className="admin-card">
          <h3>Clear Graph</h3>
          <p>Delete all nodes and edges from the Neo4j graph. Use this to start fresh before a new scan.</p>
          <button
            className="btn btn-danger"
            onClick={() => runAction('clear', api.clear)}
            disabled={loading !== null}
          >
            {loading === 'clear' ? 'Clearing...' : 'Clear Graph'}
          </button>
        </div>
      </div>

      {results && (
        <div className="card" style={{ marginTop: 24 }}>
          <div className="card-header">
            <h3>Result</h3>
          </div>
          <pre className="code-block">{JSON.stringify(results, null, 2)}</pre>
        </div>
      )}

      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
