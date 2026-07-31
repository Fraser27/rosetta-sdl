import { useEffect, useState, useRef } from 'react'
import { api, type EnrichmentJob, type EmbeddingStats } from '../api'

export default function Admin() {
  const [results, setResults] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  // Sample data state
  const [sampleLoaded, setSampleLoaded] = useState<boolean | null>(null)
  const [sampleInfo, setSampleInfo] = useState<{ datasources: number; metrics: number } | null>(null)

  // Embedding state
  const [embeddingStats, setEmbeddingStats] = useState<EmbeddingStats | null>(null)

  // Enrichment state
  const [datasources, setDatasources] = useState<{ name: string; table_count: number }[]>([])
  const [selectedDs, setSelectedDs] = useState<Set<string>>(new Set())
  const [forceEnrich, setForceEnrich] = useState(false)
  const [dsPickerOpen, setDsPickerOpen] = useState(false)
  const [modelId, setModelId] = useState('')
  const [defaultModel, setDefaultModel] = useState('')
  const [enrichJob, setEnrichJob] = useState<EnrichmentJob | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Ungoverned query-model config
  const [queryModel, setQueryModel] = useState('')
  const [availableModels, setAvailableModels] = useState<{ id: string; label: string }[]>([])
  const [savingModel, setSavingModel] = useState(false)

  // S3 Vectors search embedding-model config
  const [s3vModel, setS3vModel] = useState('')
  const [availableEmbedModels, setAvailableEmbedModels] = useState<{ id: string; label: string }[]>([])
  const [savingS3vModel, setSavingS3vModel] = useState(false)

  // Enrichment model config (free-text)
  const [enrichModel, setEnrichModel] = useState('')
  const [enrichModelDraft, setEnrichModelDraft] = useState('')
  const [savingEnrichModel, setSavingEnrichModel] = useState(false)

  const checkSampleStatus = () => {
    api.sampleDataStatus().then((s) => {
      setSampleLoaded(s.loaded)
      setSampleInfo({ datasources: s.datasources, metrics: s.metrics })
    }).catch(() => {})
  }

  const refreshEmbeddingStats = () => {
    api.embeddingStats().then(setEmbeddingStats).catch(() => {})
  }

  useEffect(() => {
    api.listDatasources().then(setDatasources).catch(() => {})
    api.getConfig().then((cfg) => {
      if (cfg.enrichment_model) setDefaultModel(cfg.enrichment_model as string)
    }).catch(() => {})
    api.getQueryModel().then((m) => {
      setQueryModel(m.query_model)
      setAvailableModels(m.available)
    }).catch(() => {})
    api.getS3VectorsModel().then((m) => {
      setS3vModel(m.s3vectors_model)
      setAvailableEmbedModels(m.available)
    }).catch(() => {})
    api.getEnrichmentModel().then((m) => {
      setEnrichModel(m.enrichment_model)
      setEnrichModelDraft(m.enrichment_model)
    }).catch(() => {})
    checkSampleStatus()
    refreshEmbeddingStats()
  }, [])

  const saveEnrichModel = async () => {
    const v = enrichModelDraft.trim()
    if (!v || v === enrichModel) return
    setSavingEnrichModel(true)
    try {
      await api.setEnrichmentModel(v)
      setEnrichModel(v)
      showToast('Enrichment model updated')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSavingEnrichModel(false)
    }
  }

  const saveS3vModel = async (modelId: string) => {
    setSavingS3vModel(true)
    try {
      await api.setS3VectorsModel(modelId)
      setS3vModel(modelId)
      showToast('S3 Vectors search model updated')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSavingS3vModel(false)
    }
  }

  const saveQueryModel = async (modelId: string) => {
    setSavingModel(true)
    try {
      await api.setQueryModel(modelId)
      setQueryModel(modelId)
      showToast('Query model updated')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSavingModel(false)
    }
  }

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
          <h3>Configurations</h3>
          <p>Model used for <strong>ungoverned</strong> (LLM-generated) SQL when no governed metric matches. Governed metrics compile deterministically and don't use an LLM.</p>
          <label style={{ display: 'block', fontSize: 13, color: 'var(--text-dim)', margin: '8px 0 4px' }}>
            Ungoverned query model
          </label>
          <select
            value={queryModel}
            onChange={(e) => saveQueryModel(e.target.value)}
            disabled={savingModel || availableModels.length === 0}
            style={{ width: '100%', maxWidth: 460, padding: '8px 10px' }}
          >
            {/* keep the current value selectable even if not in the list */}
            {!availableModels.some((m) => m.id === queryModel) && queryModel && (
              <option value={queryModel}>{queryModel} (current)</option>
            )}
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 8 }}>
            {savingModel ? 'Saving…' : <>Active: <code>{queryModel || '—'}</code> · persisted, survives restart</>}
          </p>

          <label style={{ display: 'block', fontSize: 13, color: 'var(--text-dim)', margin: '16px 0 4px' }}>
            S3 Vectors search embedding model
          </label>
          <select
            value={s3vModel}
            onChange={(e) => saveS3vModel(e.target.value)}
            disabled={savingS3vModel || availableEmbedModels.length === 0}
            style={{ width: '100%', maxWidth: 460, padding: '8px 10px' }}
          >
            {!availableEmbedModels.some((m) => m.id === s3vModel) && s3vModel && (
              <option value={s3vModel}>{s3vModel} (current)</option>
            )}
            {availableEmbedModels.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 8 }}>
            {savingS3vModel ? 'Saving…' : <>Used to embed the question before the vector search. <strong>Must match the model your documents were ingested with</strong>, or scores are meaningless.</>}
          </p>

          <label style={{ display: 'block', fontSize: 13, color: 'var(--text-dim)', margin: '16px 0 4px' }}>
            Metadata enrichment model
          </label>
          <div style={{ display: 'flex', gap: 8, maxWidth: 460 }}>
            <input
              value={enrichModelDraft}
              onChange={(e) => setEnrichModelDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') saveEnrichModel() }}
              placeholder="e.g. us.amazon.nova-2-lite-v1:0"
              style={{ flex: 1, padding: '8px 10px' }}
            />
            <button className="btn btn-primary btn-sm" onClick={saveEnrichModel}
              disabled={savingEnrichModel || !enrichModelDraft.trim() || enrichModelDraft.trim() === enrichModel}>
              {savingEnrichModel ? 'Saving…' : 'Save'}
            </button>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 8 }}>
            Bedrock modelId for LLM metadata enrichment (any Converse-capable model). Active: <code>{enrichModel || '—'}</code>
          </p>
        </div>

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

          {/* Datasource picker — collapsible */}
          {datasources.length > 0 && (
            <div style={{ margin: '12px 0' }}>
              <div
                onClick={() => setDsPickerOpen((v) => !v)}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px 12px', background: 'var(--bg-alt)', borderRadius: 6,
                  cursor: 'pointer', border: '1px solid var(--border)',
                }}
              >
                <span style={{ fontSize: 12 }}>
                  {selectedDs.size === 0
                    ? <span style={{ color: 'var(--text-dim)' }}>All datasources ({datasources.length})</span>
                    : <><strong>{selectedDs.size}</strong> of {datasources.length} datasources selected</>}
                </span>
                <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{dsPickerOpen ? '▲' : '▼'}</span>
              </div>
              {dsPickerOpen && (
                <div style={{
                  border: '1px solid var(--border)', borderTop: 'none', borderRadius: '0 0 6px 6px',
                  maxHeight: 160, overflowY: 'auto', padding: '6px 8px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
                    <button className="btn btn-ghost btn-sm" onClick={selectAllDs} style={{ fontSize: 11 }}>
                      {selectedDs.size === datasources.length ? 'Deselect All' : 'Select All'}
                    </button>
                  </div>
                  {datasources.map((ds) => (
                    <label
                      key={ds.name}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '3px 4px', fontSize: 12, cursor: 'pointer',
                        borderRadius: 4,
                        background: selectedDs.has(ds.name) ? 'var(--accent-bg, rgba(99,102,241,0.08))' : 'transparent',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedDs.has(ds.name)}
                        onChange={() => toggleDs(ds.name)}
                      />
                      {ds.name}
                      <span style={{ color: 'var(--text-dim)', marginLeft: 'auto' }}>{ds.table_count} tables</span>
                    </label>
                  ))}
                </div>
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
          <h3>Vector Embeddings</h3>
          <p>Metric embeddings enable semantic similarity search (e.g., "price" matching "revenue"). Embeddings are computed via Amazon Titan Embed V2.</p>

          {embeddingStats ? (
            <>
              <div style={{ margin: '12px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-dim)', marginBottom: 4 }}>
                  <span>
                    {embeddingStats.embedded} / {embeddingStats.total} metrics embedded
                  </span>
                  <span className={`tag ${embeddingStats.enabled ? 'tag-green' : 'tag-red'}`} style={{ fontSize: 11 }}>
                    {embeddingStats.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                {embeddingStats.total > 0 && (
                  <div style={{ height: 6, background: 'var(--bg-alt, var(--bg))', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%',
                      width: `${(embeddingStats.embedded / embeddingStats.total) * 100}%`,
                      background: embeddingStats.embedded === embeddingStats.total ? 'var(--green)' : 'var(--orange)',
                      borderRadius: 3,
                      transition: 'width 0.3s',
                    }} />
                  </div>
                )}
                <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>
                  Model: <code>{embeddingStats.model_id}</code> ({embeddingStats.dimensions}d)
                </p>
              </div>

              <button
                className="btn btn-primary"
                onClick={async () => {
                  setLoading('reembed')
                  try {
                    const res = await api.reembed()
                    showToast(`Embedded ${res.embedded}/${res.total} metrics`)
                    refreshEmbeddingStats()
                  } catch (e: unknown) { showToast((e as Error).message, 'error') }
                  finally { setLoading(null) }
                }}
                disabled={loading !== null || !embeddingStats.enabled}
              >
                {loading === 'reembed' ? 'Embedding...' : 'Reembed All Metrics'}
              </button>
            </>
          ) : (
            <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Loading stats...</p>
          )}
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
