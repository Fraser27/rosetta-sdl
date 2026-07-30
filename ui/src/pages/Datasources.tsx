import { useEffect, useState, useCallback } from 'react'
import { api } from '../api'
import type { DataSource, DataSourceRequest, TestConnectionJob } from '../api'

const REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'eu-west-1', 'eu-central-1', 'ap-southeast-1', 'ap-northeast-1']
const DS_TYPES = [
  { value: 'athena', label: 'AWS Athena' },
  { value: 'redshift_serverless', label: 'Redshift Serverless' },
]

function StatusDot({ status }: { status: string }) {
  const color = status === 'healthy' ? 'var(--green)' : status === 'unhealthy' ? 'var(--red)' : 'var(--text-dim)'
  return <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: color, marginRight: 8 }} />
}

function TestConnectionModal({ datasourceId, onClose }: { datasourceId: string; onClose: () => void }) {
  const [job, setJob] = useState<TestConnectionJob | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    let interval: ReturnType<typeof setInterval>

    async function startTest() {
      try {
        const { job_id } = await api.testDatasource(datasourceId)
        // Poll every 2 seconds
        interval = setInterval(async () => {
          if (cancelled) return
          try {
            const result = await api.pollTestConnection(datasourceId, job_id)
            setJob(result)
            if (result.status === 'success' || result.status === 'failed') {
              clearInterval(interval)
            }
          } catch (e: any) {
            setError(e.message)
            clearInterval(interval)
          }
        }, 2000)
        // Initial poll
        const initial = await api.pollTestConnection(datasourceId, job_id)
        setJob(initial)
      } catch (e: any) {
        setError(e.message)
      }
    }

    startTest()
    return () => { cancelled = true; clearInterval(interval!) }
  }, [datasourceId])

  const statusIcon = (s: string) =>
    s === 'success' ? '✓' : s === 'failed' ? '✗' : s === 'running' ? '●' : '○'
  const statusColor = (s: string) =>
    s === 'success' ? 'var(--green)' : s === 'failed' ? 'var(--red)' : s === 'running' ? 'var(--accent)' : 'var(--text-dim)'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ width: 460 }}>
        <div className="card-header" style={{ marginBottom: 20 }}>
          <h3>Testing Connection</h3>
          <button onClick={onClose} className="btn btn-ghost btn-sm">Close</button>
        </div>

        {error && <p style={{ color: 'var(--red)' }}>{error}</p>}

        {job && (
          <div className="conn-steps">
            {job.steps.map((step, i) => (
              <div key={i} className="conn-step">
                <span className="conn-step-icon" style={{ color: statusColor(step.status) }}>{statusIcon(step.status)}</span>
                <span className="conn-step-name">{step.name.replace(/_/g, ' ')}</span>
                {step.duration_ms != null && <span className="conn-step-time">{step.duration_ms.toFixed(0)}ms</span>}
                {step.error && <span className="conn-step-error">{step.error}</span>}
              </div>
            ))}
            {job.status === 'success' && (
              <div className="conn-result conn-result-ok">Connection successful</div>
            )}
            {job.status === 'failed' && (
              <div className="conn-result conn-result-fail">Connection failed{job.error ? `: ${job.error}` : ''}</div>
            )}
          </div>
        )}
        {!job && !error && <p style={{ color: 'var(--text-dim)' }}>Starting test…</p>}
      </div>
    </div>
  )
}

export default function Datasources() {
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [form, setForm] = useState<DataSourceRequest>({ name: '', type: 'redshift_serverless', endpoint: '', database: '', region: 'us-east-1' })
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await api.listDatasourcesFull()
      setDatasources(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [load])

  const handleSave = async () => {
    try {
      if (editingId) {
        await api.updateDatasource(editingId, form)
      } else {
        await api.createDatasource(form)
      }
      setShowForm(false)
      setEditingId(null)
      setForm({ name: '', type: 'redshift_serverless', endpoint: '', database: '', region: 'us-east-1' })
      await load()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this datasource?')) return
    try {
      await api.deleteDatasource(id)
      await load()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleEdit = (ds: DataSource) => {
    setEditingId(ds.datasource_id)
    setForm({ name: ds.name, type: ds.type, endpoint: ds.endpoint, database: ds.database, region: ds.region })
    setShowForm(true)
  }

  const openCreate = () => {
    setEditingId(null)
    setForm({ name: '', type: 'redshift_serverless', endpoint: '', database: '', region: 'us-east-1' })
    setError('')
    setShowForm(true)
  }

  const typeLabel = (t: string) => DS_TYPES.find(d => d.value === t)?.label ?? t
  const statusTag = (s: string) =>
    s === 'healthy' ? 'tag-green' : s === 'unhealthy' ? 'tag-red' : 'tag-blue'

  if (loading) return (
    <>
      <div className="page-header"><h2>Datasources</h2></div>
      <p style={{ color: 'var(--text-dim)' }}>Loading datasources…</p>
    </>
  )

  return (
    <>
      <div className="page-header">
        <h2>Datasources</h2>
        <p>Connect and manage the query engines behind your semantic layer</p>
      </div>

      <div style={{ marginBottom: 20 }}>
        <button className="btn btn-primary" onClick={openCreate}>+ Add Datasource</button>
      </div>

      {error && (
        <div className="conn-result conn-result-fail" style={{ marginBottom: 16 }}>{error}</div>
      )}

      {datasources.length === 0 ? (
        <div className="card empty-state">
          <p>No datasources configured yet.</p>
          <button className="btn btn-primary" onClick={openCreate}>+ Add your first datasource</button>
        </div>
      ) : (
        <div className="ds-grid">
          {datasources.map(ds => (
            <div key={ds.datasource_id} className="card ds-card">
              <div className="ds-card-top">
                <div className="ds-card-title">
                  <StatusDot status={ds.status} />
                  <strong>{ds.name}</strong>
                </div>
                <span className={`tag ${statusTag(ds.status)}`}>{typeLabel(ds.type)}</span>
              </div>

              <dl className="ds-meta">
                <div><dt>Workgroup</dt><dd>{ds.endpoint || '—'}</dd></div>
                <div><dt>Database</dt><dd>{ds.database || '—'}</dd></div>
                <div>
                  <dt>Metrics</dt>
                  <dd>
                    {ds.metric_count}
                    {ds.status === 'unhealthy' && <span className="ds-disabled"> (disabled)</span>}
                  </dd>
                </div>
                <div>
                  <dt>Last check</dt>
                  <dd>{ds.last_health_check ? new Date(ds.last_health_check).toLocaleTimeString() : 'never'}</dd>
                </div>
              </dl>

              <div className="ds-actions">
                <button className="btn btn-ghost btn-sm" onClick={() => setTestingId(ds.datasource_id)}>Test</button>
                <button className="btn btn-ghost btn-sm" onClick={() => handleEdit(ds)}>Edit</button>
                <button className="btn btn-danger btn-sm" onClick={() => handleDelete(ds.datasource_id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add/Edit Modal */}
      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="card-header" style={{ marginBottom: 20 }}>
              <h3>{editingId ? 'Edit Datasource' : 'Add Datasource'}</h3>
              <button onClick={() => setShowForm(false)} className="btn btn-ghost btn-sm">Close</button>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>Name</label>
                <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Sales Redshift" />
              </div>
              <div className="form-group">
                <label>Type</label>
                <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
                  {DS_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>{form.type === 'athena' ? 'Workgroup' : 'Workgroup'}</label>
                <input value={form.endpoint} onChange={e => setForm({ ...form, endpoint: e.target.value })} placeholder={form.type === 'athena' ? 'e.g. primary' : 'e.g. sales-workgroup'} />
              </div>
              <div className="form-group">
                <label>Database</label>
                <input value={form.database} onChange={e => setForm({ ...form, database: e.target.value })} placeholder="e.g. sales_db" />
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>Region</label>
                <select value={form.region} onChange={e => setForm({ ...form, region: e.target.value })}>
                  {REGIONS.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              {form.type === 'athena' && (
                <div className="form-group">
                  <label>Output Location (S3)</label>
                  <input value={form.output_location || ''} onChange={e => setForm({ ...form, output_location: e.target.value || null })} placeholder="s3://bucket/prefix/" />
                </div>
              )}
            </div>

            <div className="form-group">
              <label>
                Secret ARN <span className="form-hint">optional — for password auth via Secrets Manager</span>
              </label>
              <input value={form.secret_arn || ''} onChange={e => setForm({ ...form, secret_arn: e.target.value || null })} placeholder="arn:aws:secretsmanager:us-east-1:123456789012:secret:my-secret" />
            </div>

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={!form.name.trim()}>Save &amp; Test</button>
            </div>
          </div>
        </div>
      )}

      {/* Test Connection Modal */}
      {testingId && <TestConnectionModal datasourceId={testingId} onClose={() => setTestingId(null)} />}
    </>
  )
}
