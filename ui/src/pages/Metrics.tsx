import { useEffect, useState } from 'react'
import { api, type Metric, type MetricJoin } from '../api'

interface JoinRow {
  table: string
  source_column: string
  target_column: string
  join_type: string
}

const emptyJoin: JoinRow = { table: '', source_column: '', target_column: '', join_type: 'INNER' }

interface MetricForm {
  metric_id: string
  name: string
  definition: string
  expression: string
  type: string
  source_table: string
  joins: JoinRow[]
  base_metrics: string[]
  synonyms: string
  grain: string
  filters: string
}

const emptyForm: MetricForm = {
  metric_id: '', name: '', definition: '', expression: '',
  type: 'simple', source_table: '', joins: [], base_metrics: [],
  synonyms: '', grain: '', filters: '',
}

function toForm(m: Metric): MetricForm {
  return {
    metric_id: m.metric_id,
    name: m.name,
    definition: m.definition,
    expression: m.expression,
    type: m.type,
    source_table: m.source_table,
    joins: (m.joins || []).map((j) => ({
      table: j.table,
      source_column: j.source_column,
      target_column: j.target_column,
      join_type: j.join_type || 'INNER',
    })),
    base_metrics: m.base_metrics || [],
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
    source_table: f.type === 'derived' ? '' : f.source_table,
    joins: f.type === 'derived' ? [] : f.joins.filter((j) => j.table && j.source_column && j.target_column),
    base_metrics: f.type === 'derived' ? f.base_metrics : [],
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

  const addJoin = () => {
    setForm((f) => ({ ...f, joins: [...f.joins, { ...emptyJoin }] }))
  }

  const removeJoin = (idx: number) => {
    setForm((f) => ({ ...f, joins: f.joins.filter((_, i) => i !== idx) }))
  }

  const updateJoin = (idx: number, field: keyof JoinRow, value: string) => {
    setForm((f) => ({
      ...f,
      joins: f.joins.map((j, i) => i === idx ? { ...j, [field]: value } : j),
    }))
  }

  const toggleBaseMetric = (metricId: string) => {
    setForm((f) => ({
      ...f,
      base_metrics: f.base_metrics.includes(metricId)
        ? f.base_metrics.filter((id) => id !== metricId)
        : [...f.base_metrics, metricId],
    }))
  }

  // Simple metrics available as base metrics (exclude current metric being edited)
  const availableBaseMetrics = metrics.filter(
    (m) => m.type === 'simple' && m.metric_id !== form.metric_id
  )

  const formatJoins = (joins: MetricJoin[]) => {
    if (!joins || joins.length === 0) return '-'
    return joins.map((j) =>
      `${j.join_type} ${j.table} ON ${j.source_column}=${j.target_column}`
    ).join(', ')
  }

  const formatSource = (m: Metric) => {
    if (m.type === 'derived' && m.base_metrics && m.base_metrics.length > 0) {
      return m.base_metrics.map((id) => {
        const base = metrics.find((x) => x.metric_id === id)
        return base ? base.name : id
      }).join(' + ')
    }
    return m.source_table || '-'
  }

  const isDerived = form.type === 'derived'

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
              <th>Source</th>
              <th>Joins</th>
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
                <td>
                  {m.type === 'derived' ? (
                    <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{formatSource(m)}</span>
                  ) : (
                    <span className="tag tag-blue">{m.source_table}</span>
                  )}
                </td>
                <td><span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{m.type === 'derived' ? 'CTE' : formatJoins(m.joins)}</span></td>
                <td>
                  <span className={`tag ${m.type === 'derived' ? 'tag-purple' : 'tag-green'}`}>{m.type}</span>
                </td>
                <td>
                  <button className="btn btn-ghost btn-sm" onClick={() => openEdit(m)} style={{ marginRight: 6 }}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDelete(m)}>Delete</button>
                </td>
              </tr>
            ))}
            {metrics.length === 0 && (
              <tr><td colSpan={7} className="empty-state">No metrics defined yet.</td></tr>
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

            <div className="form-row">
              <div className="form-group" style={{ flex: 1 }}>
                <label>Type</label>
                <select value={form.type} onChange={(e) => updateField('type', e.target.value)}>
                  <option value="simple">Simple</option>
                  <option value="derived">Derived (composes other metrics via CTE)</option>
                </select>
              </div>
            </div>

            {isDerived ? (
              <>
                <div className="form-group">
                  <label>Base Metrics</label>
                  <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: '4px 0' }}>
                    Select the simple metrics this derived metric composes. Each becomes a CTE.
                  </p>
                  {availableBaseMetrics.length === 0 ? (
                    <p style={{ fontSize: 12, color: 'var(--orange)', margin: '4px 0' }}>
                      No simple metrics available. Create simple metrics first.
                    </p>
                  ) : (
                    <div className="base-metric-picker">
                      {availableBaseMetrics.map((m) => (
                        <label key={m.metric_id} className={`base-metric-option ${form.base_metrics.includes(m.metric_id) ? 'selected' : ''}`}>
                          <input
                            type="checkbox"
                            checked={form.base_metrics.includes(m.metric_id)}
                            onChange={() => toggleBaseMetric(m.metric_id)}
                          />
                          <span className="tag tag-blue" style={{ marginRight: 6 }}>{m.metric_id}</span>
                          <strong>{m.name}</strong>
                          <code style={{ fontSize: 11, marginLeft: 8, color: 'var(--text-dim)' }}>{m.expression}</code>
                        </label>
                      ))}
                    </div>
                  )}
                </div>

                <div className="form-group">
                  <label>Derived Expression</label>
                  <input value={form.expression} onChange={(e) => updateField('expression', e.target.value)}
                    placeholder="e.g. total_revenue - total_cost (reference base metric names)" />
                  <p style={{ fontSize: 11, color: 'var(--text-dim)', margin: '4px 0' }}>
                    Use base metric names as variables. Leave blank to return all base metrics as columns.
                  </p>
                </div>

                <div className="form-group">
                  <label>Shared Dimensions / Grain (comma-separated)</label>
                  <input value={form.grain} onChange={(e) => updateField('grain', e.target.value)}
                    placeholder="e.g. region, order_date — dimensions to GROUP BY and JOIN ON" />
                  <p style={{ fontSize: 11, color: 'var(--text-dim)', margin: '4px 0' }}>
                    Each base metric CTE will GROUP BY these, and the outer query JOINs on them.
                  </p>
                </div>
              </>
            ) : (
              <>
                <div className="form-group">
                  <label>SQL Expression</label>
                  <input value={form.expression} onChange={(e) => updateField('expression', e.target.value)}
                    placeholder="e.g. SUM(o.total_amount)" />
                </div>

                <div className="form-group">
                  <label>Source Table</label>
                  <input value={form.source_table} onChange={(e) => updateField('source_table', e.target.value)}
                    placeholder="e.g. ecommerce.orders" />
                </div>

                <div className="form-group">
                  <label>
                    Joins
                    <button className="btn btn-ghost btn-sm" onClick={addJoin} style={{ marginLeft: 8, fontSize: 11 }}>+ Add Join</button>
                  </label>
                  {form.joins.length === 0 && (
                    <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: '4px 0' }}>
                      No joins — metric runs against source table only. Add joins to include columns from other tables.
                    </p>
                  )}
                  {form.joins.map((j, idx) => (
                    <div key={idx} className="metric-join-row">
                      <select value={j.join_type} onChange={(e) => updateJoin(idx, 'join_type', e.target.value)}
                        style={{ width: 90 }}>
                        <option value="INNER">INNER</option>
                        <option value="LEFT">LEFT</option>
                        <option value="RIGHT">RIGHT</option>
                      </select>
                      <input value={j.table} onChange={(e) => updateJoin(idx, 'table', e.target.value)}
                        placeholder="Table (e.g. ecommerce.customers)" style={{ flex: 2 }} />
                      <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>ON</span>
                      <input value={j.source_column} onChange={(e) => updateJoin(idx, 'source_column', e.target.value)}
                        placeholder="Source col" style={{ flex: 1 }} />
                      <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>=</span>
                      <input value={j.target_column} onChange={(e) => updateJoin(idx, 'target_column', e.target.value)}
                        placeholder="Target col" style={{ flex: 1 }} />
                      <button className="btn btn-danger btn-sm" onClick={() => removeJoin(idx)}
                        style={{ padding: '2px 8px', fontSize: 11 }}>x</button>
                    </div>
                  ))}
                </div>

                <div className="form-group">
                  <label>Grain (comma-separated)</label>
                  <input value={form.grain} onChange={(e) => updateField('grain', e.target.value)}
                    placeholder="e.g. order_date, c.customer_name" />
                </div>
              </>
            )}

            <div className="form-group">
              <label>Synonyms (comma-separated)</label>
              <input value={form.synonyms} onChange={(e) => updateField('synonyms', e.target.value)}
                placeholder="e.g. total sales, revenue, gross revenue" />
            </div>

            <div className="form-group">
              <label>Filters (one per line)</label>
              <textarea value={form.filters} onChange={(e) => updateField('filters', e.target.value)}
                placeholder={"e.g. status != 'cancelled'"} />
            </div>

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave}
                disabled={saving || !form.metric_id || !form.name || !form.expression ||
                  (isDerived && form.base_metrics.length < 2)}>
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
