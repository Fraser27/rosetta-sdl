import { useEffect, useState, useRef } from 'react'
import { api, type EnrichmentJob } from '../api'

export default function Admin() {
  const [results, setResults] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  // Sample data state
  const [sampleLoaded, setSampleLoaded] = useState<boolean | null>(null)
  const [sampleInfo, setSampleInfo] = useState<{ datasources: number; metrics: number } | null>(null)

  // Enrichment state
  const [datasources, setDatasources] = useState<{ name: string; table_count: number }[]>([])
  const [selectedDs, setSelectedDs] = useState<Set<string>>(new Set())
  const [forceEnrich, setForceEnrich] = useState(false)
  const [modelId, setModelId] = useState('')
  const [defaultModel, setDefaultModel] = useState('')
  const [enrichJob, setEnrichJob] = useState<EnrichmentJob | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const checkSampleStatus = () => {
    api.sampleDataStatus().then((s) => {
      setSampleLoaded(s.loaded)
      setSampleInfo({ datasources: s.datasources, metrics: s.metrics })
    }).catch(() => {})
  }

  useEffect(() => {
    api.listDatasources().then(setDatasources).catch(() => {})
    api.getConfig().then((cfg) => {
      if (cfg.enrichment_model) setDefaultModel(cfg.enrichment_model as string)
    }).catch(() => {})
    checkSampleStatus()
  }, [])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

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
      // Refresh datasources after scan
      if (name === 'scan') api.listDatasources().then(setDatasources).catch(() => {})
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setLoading(null)
    }
  }

  const startEnrichment = async () => {
    setLoading('enrich')
    setResults(null)
    setEnrichJob(null)
    try {
      const dsFilter = selectedDs.size > 0 ? Array.from(selectedDs) : []
      const res = await api.enrich(dsFilter, forceEnrich, modelId)
      showToast('Enrichment started')

      // Start polling
      const jobId = res.job_id
      const poll = async () => {
        try {
          const status = await api.enrichStatus(jobId)
          setEnrichJob(status)
          if (status.status === 'completed' || status.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            setLoading(null)
            if (status.status === 'completed') {
              showToast(`Enrichment complete: ${status.tables.enriched} tables enriched`)
            } else {
              showToast(`Enrichment failed: ${status.error || 'unknown error'}`, 'error')
            }
          }
        } catch {
          // Polling error — keep trying
        }
      }
      // Poll immediately, then every 2 seconds
      poll()
      pollRef.current = setInterval(poll, 2000)
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
      setLoading(null)
    }
  }

  const toggleDs = (name: string) => {
    setSelectedDs((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const selectAllDs = () => {
    if (selectedDs.size === datasources.length) {
      setSelectedDs(new Set())
    } else {
      setSelectedDs(new Set(datasources.map((d) => d.name)))
    }
  }

  const enrichProgress = enrichJob ? (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3>Enrichment Progress</h3>
        <span className={`tag ${enrichJob.status === 'completed' ? 'tag-green' : enrichJob.status === 'failed' ? 'tag-red' : 'tag-blue'}`}>
          {enrichJob.status}
        </span>
      </div>
      <div style={{ padding: '12px 16px' }}>
        {/* Progress bar */}
        {enrichJob.tables.total > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-dim)', marginBottom: 4 }}>
              <span>Tables: {enrichJob.tables.enriched} enriched, {enrichJob.tables.skipped} skipped, {enrichJob.tables.failed} failed</span>
              <span>{enrichJob.tables.enriched + enrichJob.tables.skipped + enrichJob.tables.failed} / {enrichJob.tables.total}</span>
            </div>
            <div style={{ height: 6, background: 'var(--bg-alt)', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${((enrichJob.tables.enriched + enrichJob.tables.skipped + enrichJob.tables.failed) / enrichJob.tables.total) * 100}%`,
                background: enrichJob.tables.failed > 0 ? 'var(--orange)' : 'var(--green)',
                borderRadius: 3,
                transition: 'width 0.3s',
              }} />
            </div>
          </div>
        )}

        {enrichJob.documents.total > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-dim)', marginBottom: 4 }}>
              <span>Documents: {enrichJob.documents.enriched} enriched</span>
              <span>{enrichJob.documents.enriched} / {enrichJob.documents.total}</span>
            </div>
            <div style={{ height: 6, background: 'var(--bg-alt)', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${(enrichJob.documents.enriched / enrichJob.documents.total) * 100}%`,
                background: 'var(--accent)',
                borderRadius: 3,
                transition: 'width 0.3s',
              }} />
            </div>
          </div>
        )}

        {enrichJob.current_table && (
          <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            Currently enriching: <code>{enrichJob.current_table}</code>
          </p>
        )}

        {enrichJob.elapsed_seconds !== undefined && (
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
            Elapsed: {enrichJob.elapsed_seconds}s
          </p>
        )}

        {enrichJob.error && (
          <p style={{ fontSize: 12, color: 'var(--red)', marginTop: 8 }}>{enrichJob.error}</p>
        )}
      </div>
    </div>
  ) : null

  return (
    <>
      <div className="page-header">
        <h2>Admin</h2>
        <p>Manage the semantic layer graph: scan data sources, enrich metadata, clear graph</p>
      </div>

      <div className="admin-actions">
        <div className="admin-card">
          <h3>Sample Data</h3>
          <p>Load or remove the built-in ecommerce demo dataset (4 tables, 4 metrics, join paths, business terms).</p>

          {sampleLoaded === null ? (
            <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Checking status...</p>
          ) : sampleLoaded ? (
            <>
              <p style={{ fontSize: 12, color: 'var(--green)', margin: '8px 0' }}>
                Sample data is loaded ({sampleInfo?.metrics || 0} sample metrics)
              </p>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-danger"
                  onClick={async () => {
                    if (!confirm('Delete all sample/ecommerce data from the graph?')) return
                    setLoading('sample-delete')
                    try {
                      const res = await api.deleteSampleData()
                      setResults(res)
                      showToast('Sample data deleted')
                      checkSampleStatus()
                      api.listDatasources().then(setDatasources).catch(() => {})
                    } catch (e: unknown) { showToast((e as Error).message, 'error') }
                    finally { setLoading(null) }
                  }}
                  disabled={loading !== null}
                >
                  {loading === 'sample-delete' ? 'Deleting...' : 'Delete Sample Data'}
                </button>
                <button
                  className="btn btn-secondary"
                  onClick={async () => {
                    setLoading('sample-reload')
                    try {
                      await api.deleteSampleData()
                      const res = await api.loadSampleData()
                      setResults(res)
                      showToast('Sample data reloaded')
                      checkSampleStatus()
                      api.listDatasources().then(setDatasources).catch(() => {})
                    } catch (e: unknown) { showToast((e as Error).message, 'error') }
                    finally { setLoading(null) }
                  }}
                  disabled={loading !== null}
                >
                  {loading === 'sample-reload' ? 'Reloading...' : 'Reload'}
                </button>
              </div>
            </>
          ) : (
            <>
              <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: '8px 0' }}>
                No sample data loaded
              </p>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  setLoading('sample-load')
                  try {
                    const res = await api.loadSampleData()
                    setResults(res)
                    showToast('Sample data loaded')
                    checkSampleStatus()
                    api.listDatasources().then(setDatasources).catch(() => {})
                  } catch (e: unknown) { showToast((e as Error).message, 'error') }
                  finally { setLoading(null) }
                }}
                disabled={loading !== null}
              >
                {loading === 'sample-load' ? 'Loading...' : 'Load Sample Data'}
              </button>
            </>
          )}
        </div>

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
          <p>Use LLM (Bedrock) to generate descriptions for tables and columns, extract concepts from documents, and create business term mappings. Tables and columns with existing descriptions are skipped.</p>
          <p style={{ fontSize: 11, color: 'var(--orange)', margin: '8px 0' }}>
            Requires Bedrock model access. Ensure the EC2 IAM role has <code>bedrock:InvokeModel</code> permission.
          </p>

          {/* Datasource picker */}
          {datasources.length > 0 && (
            <div style={{ margin: '12px 0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-dim)' }}>Select DataSources</label>
                <button className="btn btn-ghost btn-sm" onClick={selectAllDs} style={{ fontSize: 11 }}>
                  {selectedDs.size === datasources.length ? 'Deselect All' : 'Select All'}
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {datasources.map((ds) => (
                  <label
                    key={ds.name}
                    className={`base-metric-option ${selectedDs.has(ds.name) ? 'selected' : ''}`}
                    style={{ padding: '4px 10px', fontSize: 12 }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedDs.has(ds.name)}
                      onChange={() => toggleDs(ds.name)}
                    />
                    {ds.name}
                    <span style={{ color: 'var(--text-dim)', marginLeft: 4 }}>({ds.table_count} tables)</span>
                  </label>
                ))}
              </div>
              {selectedDs.size === 0 && (
                <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                  No datasources selected — will enrich all.
                </p>
              )}
            </div>
          )}

          {/* Model ID */}
          <div style={{ margin: '12px 0' }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>Bedrock Model ID</label>
            <input
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder={defaultModel || 'e.g. anthropic.claude-haiku-4-5-20251001'}
              style={{ width: '100%', maxWidth: 400, fontSize: 13 }}
            />
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>
              Leave blank to use default: <code>{defaultModel || 'loading...'}</code>
            </p>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
            <button
              className="btn btn-primary"
              onClick={startEnrichment}
              disabled={loading !== null}
            >
              {loading === 'enrich' ? 'Enriching...' : 'Start Enrichment'}
            </button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-dim)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={forceEnrich}
                onChange={(e) => setForceEnrich(e.target.checked)}
              />
              Force re-enrich (overwrite existing descriptions)
            </label>
          </div>

          {enrichProgress}
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
