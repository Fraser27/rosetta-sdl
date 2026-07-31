import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, type TableDetail as TableDetailType } from '../api'

const MAX_DESC_WORDS = 50
const wordCount = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

function InlineEdit({ value, onSave }: { value: string; onSave: (v: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const words = wordCount(draft)
  const overLimit = words > MAX_DESC_WORDS

  const handleSave = async () => {
    if (draft === value) { setEditing(false); return }
    if (overLimit) { setError(`${words}/${MAX_DESC_WORDS} words — too long`); return }
    setSaving(true)
    setError('')
    try {
      await onSave(draft)
      setEditing(false)
    } catch (e: unknown) {
      // Surface the server-side cap rejection (422) rather than closing silently.
      setError((e as Error).message.replace(/^\d+:\s*/, ''))
    } finally {
      setSaving(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') { setDraft(value); setError(''); setEditing(false) }
  }

  if (!editing) {
    return (
      <span
        onClick={() => setEditing(true)}
        style={{ cursor: 'pointer', color: 'var(--text-dim)', fontSize: 13, borderBottom: '1px dashed var(--border)' }}
        title="Click to edit"
      >
        {value || 'Click to add description...'}
      </span>
    )
  }

  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={handleSave}
        autoFocus
        style={{ fontSize: 13, padding: '2px 6px', minWidth: 240, borderColor: overLimit ? 'var(--red)' : undefined }}
      />
      <span style={{ fontSize: 11, color: overLimit ? 'var(--red)' : 'var(--text-dim)' }}>{words}/{MAX_DESC_WORDS}</span>
      {saving && <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>saving...</span>}
      {error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{error}</span>}
    </span>
  )
}

export default function TableDetail() {
  const { name } = useParams<{ name: string }>()
  const [table, setTable] = useState<TableDetailType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)

  const load = () => {
    if (!name) return
    api.getTable(name)
      .then(setTable)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [name])

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const handleTableDescSave = async (desc: string) => {
    if (!name) return
    await api.updateTableDescription(name, desc)
    setTable((t) => t ? { ...t, description: desc } : t)
    showToast('Table description updated')
  }

  const handleColumnDescSave = async (colName: string, desc: string) => {
    if (!name) return
    await api.updateColumnDescription(name, colName, desc)
    setTable((t) => {
      if (!t) return t
      return {
        ...t,
        columns: t.columns.map((c) =>
          c.name === colName ? { ...c, description: desc } : c
        ),
      }
    })
    showToast(`Column "${colName}" description updated`)
  }

  const handleToggleDeprecation = async (colName: string, next: boolean) => {
    if (!name) return
    try {
      await api.updateColumnDeprecation(name, colName, next)
      setTable((t) => {
        if (!t) return t
        return {
          ...t,
          columns: t.columns.map((c) =>
            c.name === colName ? { ...c, is_deprecated: next } : c
          ),
        }
      })
      showToast(`Column "${colName}" ${next ? 'marked deprecated' : 'un-deprecated'}`)
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error: {error}</div>
  if (!table) return <div className="empty-state">Table not found</div>

  return (
    <>
      <Link to="/tables" className="back-link">&larr; Back to Tables</Link>

      <div className="page-header">
        <h2>{table.name}</h2>
        <InlineEdit value={table.description || ''} onSave={handleTableDescSave} />
      </div>

      <div className="detail-grid">
        <div>
          <div className="detail-field">
            <div className="label">Full Name</div>
            <div className="value">{table.full_name}</div>
          </div>
          <div className="detail-field">
            <div className="label">Database</div>
            <div className="value"><span className="tag tag-blue">{table.database}</span></div>
          </div>
          <div className="detail-field">
            <div className="label">Catalog Type</div>
            <div className="value"><span className="tag tag-purple">{table.catalog_type}</span></div>
          </div>
        </div>
        <div>
          {table.joins && table.joins.length > 0 && (
            <div className="detail-field">
              <div className="label">Join Paths</div>
              {table.joins.map((j, i) => (
                <div key={i} style={{ marginBottom: 8 }}>
                  <span className="tag tag-orange">{j.join_type}</span>{' '}
                  <Link to={`/tables/${j.related_table}`}>{j.related_table}</Link>{' '}
                  ON <code style={{ fontSize: 12 }}>{j.on_column}</code>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-header">
          <h3>Columns ({table.columns.length})</h3>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Data Type</th>
              <th>Description</th>
              <th>Partition</th>
              <th>Primary Key</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map((c) => (
              <tr key={c.name} style={c.is_deprecated ? { opacity: 0.6 } : undefined}>
                <td><strong style={c.is_deprecated ? { textDecoration: 'line-through' } : undefined}>{c.name}</strong></td>
                <td><code style={{ fontSize: 12 }}>{c.data_type}</code></td>
                <td>
                  {c.is_deprecated ? (
                    // Deprecation takes precedence over the description.
                    <span style={{ fontSize: 13, color: 'var(--red)' }} title={c.description || undefined}>
                      Deprecated — avoid using this column
                    </span>
                  ) : (
                    <InlineEdit
                      value={c.description || ''}
                      onSave={(desc) => handleColumnDescSave(c.name, desc)}
                    />
                  )}
                </td>
                <td>{c.is_partition && <span className="tag tag-orange">partition</span>}</td>
                <td>{c.is_primary_key && <span className="tag tag-green">PK</span>}</td>
                <td>
                  {c.is_deprecated ? (
                    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                      <span className="tag tag-red">deprecated</span>
                      <button className="btn btn-ghost btn-sm" onClick={() => handleToggleDeprecation(c.name, false)}>Restore</button>
                    </span>
                  ) : (
                    <button className="btn btn-ghost btn-sm" onClick={() => handleToggleDeprecation(c.name, true)}>Deprecate</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
