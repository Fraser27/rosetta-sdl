import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Metric, type MetricJoin, type TableSummary, type Column } from '../api'

interface JoinRow {
  table: string
  source_column: string
  target_column: string
  join_type: string
}

const emptyJoin: JoinRow = { table: '', source_column: '', target_column: '', join_type: 'INNER' }

interface ParameterRow {
  column: string
  operator: string
  required: boolean
  description: string
}

const emptyParameter: ParameterRow = { column: '', operator: '=', required: false, description: '' }

interface MetricForm {
  metric_id: string
  name: string
  definition: string
  expression: string
  type: string
  source_db: string
  source_table: string
  joins: JoinRow[]
  parameters: ParameterRow[]
  base_metrics: string[]
  synonyms: string
  grain: string
  filters: string
}

const emptyForm: MetricForm = {
  metric_id: '', name: '', definition: '', expression: '',
  type: 'simple', source_db: '', source_table: '', joins: [], parameters: [],
  base_metrics: [], synonyms: '', grain: '', filters: '',
}

function toForm(m: Metric): MetricForm {
  const sourceDb = m.source_table ? m.source_table.split('.')[0] || '' : ''
  return {
    metric_id: m.metric_id,
    name: m.name,
    definition: m.definition,
    expression: m.expression,
    type: m.type,
    source_db: sourceDb,
    source_table: m.source_table,
    joins: (m.joins || []).map((j) => ({
      table: j.table,
      source_column: j.source_column,
      target_column: j.target_column,
      join_type: j.join_type || 'INNER',
    })),
    parameters: (m.parameters || []).map((p) => ({
      column: p.column,
      operator: p.operator || '=',
      required: p.required || false,
      description: p.description || '',
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
    parameters: f.type === 'derived' ? [] : f.parameters.filter((p) => p.column),
    base_metrics: f.type === 'derived' ? f.base_metrics : [],
    synonyms: f.synonyms ? f.synonyms.split(',').map((s) => s.trim()).filter(Boolean) : [],
    grain: f.grain ? f.grain.split(',').map((s) => s.trim()).filter(Boolean) : [],
    filters: f.filters ? f.filters.split('\n').map((s) => s.trim()).filter(Boolean) : [],
  }
}

export default function Metrics() {
  const navigate = useNavigate()
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [tables, setTables] = useState<TableSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<Metric | null>(null)
  const [form, setForm] = useState<MetricForm>(emptyForm)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  // Column cache: full_name -> Column[]
  const [columnCache, setColumnCache] = useState<Record<string, Column[]>>({})

  // SQL preview state
  const [previewSql, setPreviewSql] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  // Expanded SQL row in table
  const [expandedSql, setExpandedSql] = useState<Record<string, string | null>>({})
  const [sqlLoading, setSqlLoading] = useState<Record<string, boolean>>({})

  const load = () => {
    Promise.all([api.listMetrics(), api.listTables()])
      .then(([m, t]) => { setMetrics(m); setTables(t) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // Derived data
  const databases = useMemo(() =>
    [...new Set(tables.map((t) => t.database))].sort(),
    [tables],
  )

  const tablesByDb = useMemo(() => {
    const map: Record<string, TableSummary[]> = {}
    for (const t of tables) {
      ;(map[t.database] ||= []).push(t)
    }
    return map
  }, [tables])

  // Fetch columns for a table (cached)
  const fetchColumns = async (fullName: string) => {
    if (columnCache[fullName]) return columnCache[fullName]
    try {
      const detail = await api.getTable(fullName)
      const cols = detail.columns || []
      setColumnCache((c) => ({ ...c, [fullName]: cols }))
      return cols
    } catch {
      return []
    }
  }

  // Pre-fetch columns when source table changes
  useEffect(() => {
    if (form.source_table) fetchColumns(form.source_table)
  }, [form.source_table])

  // Pre-fetch columns for join tables
  useEffect(() => {
    for (const j of form.joins) {
      if (j.table) fetchColumns(j.table)
    }
  }, [form.joins.map((j) => j.table).join(',')])

  const sourceColumns = columnCache[form.source_table] || []

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const openCreate = () => {
    setEditing(null)
    setForm(emptyForm)
    setPreviewSql(null)
    setShowModal(true)
  }

  const openEdit = (m: Metric) => {
    setEditing(m)
    setForm(toForm(m))
    setPreviewSql(null)
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
      // Auto-compile after save to show SQL
      setTimeout(() => handleViewSql(form.metric_id), 500)
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
      setExpandedSql((s) => { const next = { ...s }; delete next[m.metric_id]; return next })
      load()
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    }
  }

  // Preview SQL in the modal (for editing existing metrics)
  const handlePreviewSql = async () => {
    if (!editing) return
    setPreviewLoading(true)
    try {
      const res = await api.compileMetric(editing.metric_id)
      setPreviewSql(res.sql)
    } catch (e: unknown) {
      setPreviewSql(`-- Error: ${(e as Error).message}`)
    } finally {
      setPreviewLoading(false)
    }
  }

  // View SQL for a metric in the table
  const handleViewSql = async (metricId: string) => {
    if (expandedSql[metricId] !== undefined) {
      // Toggle off
      setExpandedSql((s) => { const next = { ...s }; delete next[metricId]; return next })
      return
    }
    setSqlLoading((s) => ({ ...s, [metricId]: true }))
    try {
      const res = await api.compileMetric(metricId)
      setExpandedSql((s) => ({ ...s, [metricId]: res.sql }))
    } catch (e: unknown) {
      setExpandedSql((s) => ({ ...s, [metricId]: `-- Error: ${(e as Error).message}` }))
    } finally {
      setSqlLoading((s) => ({ ...s, [metricId]: false }))
    }
  }

  const updateField = (field: keyof MetricForm, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
    setPreviewSql(null)
  }

  const setSourceDb = (db: string) => {
    setForm((f) => ({ ...f, source_db: db, source_table: '' }))
    setPreviewSql(null)
  }

  const setSourceTable = (fullName: string) => {
    setForm((f) => ({ ...f, source_table: fullName }))
    setPreviewSql(null)
  }

  const addJoin = () => {
    setForm((f) => ({ ...f, joins: [...f.joins, { ...emptyJoin }] }))
    setPreviewSql(null)
  }

  const removeJoin = (idx: number) => {
    setForm((f) => ({ ...f, joins: f.joins.filter((_, i) => i !== idx) }))
    setPreviewSql(null)
  }

  const updateJoin = (idx: number, field: keyof JoinRow, value: string) => {
    setForm((f) => ({
      ...f,
      joins: f.joins.map((j, i) => {
        if (i !== idx) return j
        const updated = { ...j, [field]: value }
        if (field === 'table') {
          updated.target_column = ''
        }
        return updated
      }),
    }))
    setPreviewSql(null)
  }

  const addParameter = () => {
    setForm((f) => ({ ...f, parameters: [...f.parameters, { ...emptyParameter }] }))
    setPreviewSql(null)
  }

  const removeParameter = (idx: number) => {
    setForm((f) => ({ ...f, parameters: f.parameters.filter((_, i) => i !== idx) }))
    setPreviewSql(null)
  }

  const updateParameter = (idx: number, field: keyof ParameterRow, value: string | boolean) => {
    setForm((f) => ({
      ...f,
      parameters: f.parameters.map((p, i) => i === idx ? { ...p, [field]: value } : p),
    }))
    setPreviewSql(null)
  }

  const toggleBaseMetric = (metricId: string) => {
    setForm((f) => ({
      ...f,
      base_metrics: f.base_metrics.includes(metricId)
        ? f.base_metrics.filter((id) => id !== metricId)
        : [...f.base_metrics, metricId],
    }))
    setPreviewSql(null)
  }

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

  const [metricFilter, setMetricFilter] = useState('')

  const filteredMetrics = useMemo(() => {
    if (!metricFilter.trim()) return metrics
    const q = metricFilter.toLowerCase()
    return metrics.filter((m) =>
      m.metric_id.toLowerCase().includes(q) ||
      m.name.toLowerCase().includes(q) ||
      (m.definition || '').toLowerCase().includes(q) ||
      m.expression.toLowerCase().includes(q) ||
      (m.source_table || '').toLowerCase().includes(q) ||
      m.type.toLowerCase().includes(q) ||
      (m.synonyms || []).some((s) => s.toLowerCase().includes(q)) ||
      (m.source || '').toLowerCase().includes(q)
    )
  }, [metrics, metricFilter])

  const isDerived = form.type === 'derived'
  const filteredTables = form.source_db ? (tablesByDb[form.source_db] || []) : tables

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Metrics</h2>
        <p>Governed business metrics with deterministic SQL compilation</p>
      </div>

      <div style={{ marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center' }}>
        <button className="btn btn-primary" onClick={openCreate}>+ New Metric</button>
        <div className="search-bar" style={{ flex: 1, margin: 0 }}>
          <input
            placeholder="Filter metrics by name, ID, expression, source, synonyms..."
            value={metricFilter}
            onChange={(e) => setMetricFilter(e.target.value)}
          />
        </div>
        {metricFilter && (
          <span style={{ fontSize: 12, color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
            {filteredMetrics.length} of {metrics.length}
          </span>
        )}
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
            {filteredMetrics.map((m) => (
              <>
                <tr key={m.metric_id}>
                  <td><span className="tag tag-blue">{m.metric_id}</span></td>
                  <td>
                    <strong>{m.name}</strong>
                    {m.source && m.source !== 'user' && (
                      <span className="tag" style={{ marginLeft: 6, fontSize: 10, background: 'var(--bg-alt)', color: 'var(--text-dim)', border: '1px solid var(--border)' }}>
                        {m.source}
                      </span>
                    )}
                    <br /><span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{m.definition}</span>
                  </td>
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
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button
                      className={`btn btn-sm ${expandedSql[m.metric_id] !== undefined ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => handleViewSql(m.metric_id)}
                      disabled={sqlLoading[m.metric_id]}
                      style={{ marginRight: 6 }}
                    >
                      {sqlLoading[m.metric_id] ? '...' : 'SQL'}
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/graph?metric=${encodeURIComponent(m.name)}`)} style={{ marginRight: 6 }}>Graph</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => openEdit(m)} style={{ marginRight: 6 }}>Edit</button>
                    <button className="btn btn-danger btn-sm" onClick={() => handleDelete(m)}>Delete</button>
                  </td>
                </tr>
                {expandedSql[m.metric_id] !== undefined && (
                  <tr key={`${m.metric_id}-sql`}>
                    <td colSpan={7} style={{ padding: 0 }}>
                      <pre className="code-block" style={{ margin: 0, borderRadius: 0, borderTop: 'none' }}>
                        {expandedSql[m.metric_id]}
                      </pre>
                    </td>
                  </tr>
                )}
              </>
            ))}
            {filteredMetrics.length === 0 && (
              <tr><td colSpan={7} className="empty-state">{metricFilter ? 'No metrics match your filter.' : 'No metrics defined yet.'}</td></tr>
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

                <div className="form-row">
                  <div className="form-group">
                    <label>Database</label>
                    <select value={form.source_db} onChange={(e) => setSourceDb(e.target.value)}>
                      <option value="">-- Select database --</option>
                      {databases.map((db) => (
                        <option key={db} value={db}>{db}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Source Table</label>
                    <select value={form.source_table} onChange={(e) => setSourceTable(e.target.value)}
                      disabled={!form.source_db}>
                      <option value="">{form.source_db ? '-- Select table --' : '-- Select database first --'}</option>
                      {filteredTables.map((t) => (
                        <option key={t.full_name} value={t.full_name}>{t.name}</option>
                      ))}
                    </select>
                  </div>
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
                  {form.joins.map((j, idx) => {
                    const joinCols = columnCache[j.table] || []
                    return (
                      <div key={idx} className="metric-join-row">
                        <select value={j.join_type} onChange={(e) => updateJoin(idx, 'join_type', e.target.value)}
                          style={{ width: 90 }}>
                          <option value="INNER">INNER</option>
                          <option value="LEFT">LEFT</option>
                          <option value="RIGHT">RIGHT</option>
                        </select>
                        <select value={j.table} onChange={(e) => updateJoin(idx, 'table', e.target.value)}
                          style={{ flex: 2 }}>
                          <option value="">-- Join table --</option>
                          {databases.map((db) => (
                            <optgroup key={db} label={db}>
                              {(tablesByDb[db] || [])
                                .filter((t) => t.full_name !== form.source_table)
                                .map((t) => (
                                  <option key={t.full_name} value={t.full_name}>{t.name}</option>
                                ))}
                            </optgroup>
                          ))}
                        </select>
                        <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>ON</span>
                        <select value={j.source_column} onChange={(e) => updateJoin(idx, 'source_column', e.target.value)}
                          style={{ flex: 1 }} disabled={!form.source_table}>
                          <option value="">{sourceColumns.length ? '-- Source col --' : 'Select source table'}</option>
                          {sourceColumns.map((c) => (
                            <option key={c.name} value={c.name}>{c.name}</option>
                          ))}
                        </select>
                        <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>=</span>
                        <select value={j.target_column} onChange={(e) => updateJoin(idx, 'target_column', e.target.value)}
                          style={{ flex: 1 }} disabled={!j.table}>
                          <option value="">{joinCols.length ? '-- Target col --' : 'Select join table'}</option>
                          {joinCols.map((c) => (
                            <option key={c.name} value={c.name}>{c.name}</option>
                          ))}
                        </select>
                        <button className="btn btn-danger btn-sm" onClick={() => removeJoin(idx)}
                          style={{ padding: '2px 8px', fontSize: 11 }}>x</button>
                      </div>
                    )
                  })}
                </div>

                <div className="form-group">
                  <label>Grain (comma-separated)</label>
                  <input value={form.grain} onChange={(e) => updateField('grain', e.target.value)}
                    placeholder="e.g. order_date, c.customer_name" />
                </div>

                <div className="form-group">
                  <label>
                    Parameters
                    <button className="btn btn-ghost btn-sm" onClick={addParameter} style={{ marginLeft: 8, fontSize: 11 }}>+ Add Parameter</button>
                  </label>
                  <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: '4px 0' }}>
                    Declare columns that accept runtime filter values via query_metric. If none, any filter is accepted.
                  </p>
                  {form.parameters.map((p, idx) => (
                    <div key={idx} className="metric-join-row">
                      <select value={p.column} onChange={(e) => updateParameter(idx, 'column', e.target.value)}
                        style={{ flex: 2 }}>
                        <option value="">-- Column --</option>
                        {sourceColumns.map((c) => (
                          <option key={c.name} value={c.name}>{c.name}</option>
                        ))}
                      </select>
                      <select value={p.operator} onChange={(e) => updateParameter(idx, 'operator', e.target.value)}
                        style={{ width: 70 }}>
                        <option value="=">=</option>
                        <option value="!=">!=</option>
                        <option value=">">{'>'}</option>
                        <option value="<">{'<'}</option>
                        <option value=">=">{'≥'}</option>
                        <option value="<=">{'≤'}</option>
                        <option value="IN">IN</option>
                        <option value="LIKE">LIKE</option>
                      </select>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-dim)' }}>
                        <input type="checkbox" checked={p.required}
                          onChange={(e) => updateParameter(idx, 'required', e.target.checked)} />
                        Required
                      </label>
                      <input value={p.description} onChange={(e) => updateParameter(idx, 'description', e.target.value)}
                        placeholder="Description (optional)" style={{ flex: 2 }} />
                      <button className="btn btn-danger btn-sm" onClick={() => removeParameter(idx)}
                        style={{ padding: '2px 8px', fontSize: 11 }}>x</button>
                    </div>
                  ))}
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

            {/* SQL Preview */}
            {editing && (
              <div className="form-group">
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={handlePreviewSql}
                  disabled={previewLoading}
                  style={{ marginBottom: 8 }}
                >
                  {previewLoading ? 'Compiling...' : 'Preview Compiled SQL'}
                </button>
                {previewSql && (
                  <pre className="code-block" style={{ fontSize: 12 }}>{previewSql}</pre>
                )}
              </div>
            )}

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave}
                disabled={saving || !form.metric_id || !form.name || !form.expression ||
                  (isDerived && form.base_metrics.length < 2) ||
                  (!isDerived && !form.source_table)}>
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
