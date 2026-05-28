import { useEffect, useState, useCallback } from 'react'
import { api, DataSource, DataSourceRequest, TestConnectionJob } from '../api'

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

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ minWidth: 400 }}>
        <div className="modal-header">
          <h3>Testing Connection...</h3>
          <button onClick={onClose} className="btn-icon">X</button>
        </div>
        <div className="modal-body">
          {error && <p style={{ color: 'var(--red)' }}>{error}</p>}
          {job && (
            <div>
              {job.steps.map((step, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span>
                    {step.status === 'success' ? '✓' : step.status === 'failed' ? '✗' : step.status === 'running' ? '●' : '○'}
                  </span>
                  <span style={{ flex: 1 }}>{step.name.replace(/_/g, ' ')}</span>
                  {step.duration_ms != null && <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>{step.duration_ms.toFixed(0)}ms</span>}
                  {step.error && <span style={{ color: 'var(--red)', fontSize: 12 }}>{step.error}</span>}
                </div>
              ))}
              {job.status === 'success' && <p style={{ color: 'var(--green)', marginTop: 12 }}>Connection successful!</p>}
              {job.status === 'failed' && <p style={{ color: 'var(--red)', marginTop: 12 }}>Connection failed: {job.error}</p>}
            </div>
          )}
          {!job && !error && <p>Starting test...</p>}
        </div>
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

  if (loading) return <div className="page"><p>Loading datasources...</p></div>

  return (
    <div className="page">
      <div className="page-header">
        <h2>Datasources</h2>
        <button className="btn-primary" onClick={() => { setEditingId(null); setForm({ name: '', type: 'redshift_serverless', endpoint: '', database: '', region: 'us-east-1' }); setShowForm(true) }}>
          + Add New
        </button>
      </div>

      {error && <p style={{ color: 'var(--red)' }}>{error}</p>}

      <div className="cards-grid">
        {datasources.map(ds => (
          <div key={ds.datasource_id} className="card">
            <div className="card-header">
              <StatusDot status={ds.status} />
              <strong>{ds.name}</strong>
              <span className="badge" style={{ marginLeft: 8 }}>{ds.type}</span>
            </div>
            <div className="card-body" style={{ fontSize: 13, color: 'var(--text-dim)' }}>
              <p>Workgroup: {ds.endpoint} | Database: {ds.database || '—'}</p>
              <p>
                Metrics: {ds.metric_count}
                {ds.status === 'unhealthy' && <span style={{ color: 'var(--red)' }}> (disabled)</span>}
                {' | '}Last check: {ds.last_health_check ? new Date(ds.last_health_check).toLocaleTimeString() : 'never'}
              </p>
            </div>
            <div className="card-actions">
              <button className="btn-sm" onClick={() => setTestingId(ds.datasource_id)}>Test</button>
              <button className="btn-sm" onClick={() => handleEdit(ds)}>Edit</button>
              <button className="btn-sm btn-danger" onClick={() => handleDelete(ds.datasource_id)}>Delete</button>
            </div>
          </div>
        ))}
        {datasources.length === 0 && <p>No datasources configured. Click "Add New" to get started.</p>}
      </div>

      {/* Add/Edit Modal */}
      {showForm && (
        <div className="modal-backdrop" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{editingId ? 'Edit Datasource' : 'Add Datasource'}</h3>
              <button onClick={() => setShowForm(false)} className="btn-icon">X</button>
            </div>
            <div className="modal-body">
              <label>Name
                <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Sales Redshift" />
              </label>
              <label>Type
                <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
                  {DS_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <label>Workgroup
                <input value={form.endpoint} onChange={e => setForm({ ...form, endpoint: e.target.value })} placeholder="e.g. sales-workgroup" />
              </label>
              <label>Database
                <input value={form.database} onChange={e => setForm({ ...form, database: e.target.value })} placeholder="e.g. sales_db" />
              </label>
              <label>Region
                <select value={form.region} onChange={e => setForm({ ...form, region: e.target.value })}>
                  {REGIONS.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
              <label>Secret ARN <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>(optional — for password auth)</span>
                <input value={form.secret_arn || ''} onChange={e => setForm({ ...form, secret_arn: e.target.value || null })} placeholder="arn:aws:secretsmanager:..." />
              </label>
              {form.type === 'athena' && (
                <label>Output Location (S3)
                  <input value={form.output_location || ''} onChange={e => setForm({ ...form, output_location: e.target.value || null })} placeholder="s3://bucket/prefix/" />
                </label>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn-primary" onClick={handleSave}>Save & Test</button>
            </div>
          </div>
        </div>
      )}

      {/* Test Connection Modal */}
      {testingId && <TestConnectionModal datasourceId={testingId} onClose={() => setTestingId(null)} />}
    </div>
  )
}
