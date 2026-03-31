import { useEffect, useState } from 'react'
import { api, type Metric } from '../api'

interface MetricForm {
  metric_id: string
  name: string
  definition: string
  expression: string
  type: string
  source_table: string
  synonyms: string
  grain: string
  filters: string
}

const emptyForm: MetricForm = {
  metric_id: '', name: '', definition: '', expression: '',
  type: 'simple', source_table: '', synonyms: '', grain: '', filters: '',
}

function toForm(m: Metric): MetricForm {
  return {
    metric_id: m.metric_id,
    name: m.name,
    definition: m.definition,
    expression: m.expression,
    type: m.type,
    source_table: m.source_table,
    synonyms: (m.synonyms || []).join(', '),
    grain: (m.grain || []).join(', '),
    filters: (m.filters || []).join('\n'),
  }
}

function fromForm(f: MetricForm) {
  return {
    metric_id: f.metric_id,
    name: f.name,
    definition: f.definition,
    expression: f.expression,
    type: f.type,
    source_table: f.source_table,
    synonyms: f.synonyms ? f.synonyms.split(',').map((s) => s.trim()).filter(Boolean) : [],
    grain: f.grain ? f.grain.split(',').map((s) => s.trim()).filter(Boolean) : [],
    filters: f.filters ? f.filters.split('\n').map((s) => s.trim()).filter(Boolean) : [],
  }
}

export default function Metrics() {
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<Metric | null>(null)
  const [form, setForm] = useState<MetricForm>(emptyForm)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  const load = () => {
    api.listMetrics()
      .then(setMetrics)
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const openCreate = () => {
    setEditing(null)
    setForm(emptyForm)
    setShowModal(true)
  }

  const openEdit = (m: Metric) => {
    setEditing(m)
    setForm(toForm(m))
    setShowModal(true)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const data = fromForm(form)
      if (editing) {
        await api.updateMetric(editing.metric_id, data)
        showToast(`Updated metric "${form.name}"`)
      } else {
        await api.createMetric(data)
        showToast(`Created metric "${form.name}"`)
      }
      setShowModal(false)
      load()
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (m: Metric) => {
    if (!confirm(`Delete metric "${m.name}"?`)) return
    try {
      await api.deleteMetric(m.metric_id)
      showToast(`Deleted metric "${m.name}"`)
      load()
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    }
  }

  const updateField = (field: keyof MetricForm, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
  }

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Metrics</h2>
        <p>Governed business metrics with deterministic SQL compilation</p>
      </div>

      <div style={{ marginBottom: 20 }}>
        <button className="btn btn-primary" onClick={openCreate}>+ New Metric</button>
      </div>

      <div className="card">
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Expression</th>
              <th>Source Table</th>
              <th>Type</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.metric_id}>
                <td><span className="tag tag-blue">{m.metric_id}</span></td>
                <td><strong>{m.name}</strong><br /><span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{m.definition}</span></td>
                <td><code style={{ fontSize: 12 }}>{m.expression}</code></td>
                <td><span className="tag tag-blue">{m.source_table}</span></td>
                <td><span className="tag tag-green">{m.type}</span></td>
                <td>
                  <button className="btn btn-ghost btn-sm" onClick={() => openEdit(m)} style={{ marginRight: 6 }}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDelete(m)}>Delete</button>
                </td>
              </tr>
            ))}
            {metrics.length === 0 && (
              <tr><td colSpan={6} className="empty-state">No metrics defined yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{editing ? 'Edit Metric' : 'New Metric'}</h3>

            <div className="form-row">
              <div className="form-group">
                <label>Metric ID</label>
                <input value={form.metric_id} onChange={(e) => updateField('metric_id', e.target.value)}
                  placeholder="e.g. m_007" disabled={!!editing} />
              </div>
              <div className="form-group">
                <label>Name</label>
                <input value={form.name} onChange={(e) => updateField('name', e.target.value)}
                  placeholder="e.g. total_revenue" />
              </div>
            </div>

            <div className="form-group">
              <label>Definition</label>
              <input value={form.definition} onChange={(e) => updateField('definition', e.target.value)}
                placeholder="Human-readable description of this metric" />
            </div>

            <div className="form-group">
              <label>SQL Expression</label>
              <input value={form.expression} onChange={(e) => updateField('expression', e.target.value)}
                placeholder="e.g. SUM(total_amount)" />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>Source Table</label>
                <input value={form.source_table} onChange={(e) => updateField('source_table', e.target.value)}
                  placeholder="e.g. ecommerce.orders" />
              </div>
              <div className="form-group">
                <label>Type</label>
                <select value={form.type} onChange={(e) => updateField('type', e.target.value)}>
                  <option value="simple">Simple</option>
                  <option value="derived">Derived</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label>Synonyms (comma-separated)</label>
              <input value={form.synonyms} onChange={(e) => updateField('synonyms', e.target.value)}
                placeholder="e.g. total sales, revenue, gross revenue" />
            </div>

            <div className="form-group">
              <label>Grain (comma-separated)</label>
              <input value={form.grain} onChange={(e) => updateField('grain', e.target.value)}
                placeholder="e.g. order_date, customer_id" />
            </div>

            <div className="form-group">
              <label>Filters (one per line)</label>
              <textarea value={form.filters} onChange={(e) => updateField('filters', e.target.value)}
                placeholder={"e.g. status != 'cancelled'"} />
            </div>

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving || !form.metric_id || !form.name || !form.expression}>
                {saving ? 'Saving...' : editing ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
