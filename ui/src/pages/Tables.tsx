import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type TableSummary } from '../api'

export default function Tables() {
  const [tables, setTables] = useState<TableSummary[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listTables()
      .then(setTables)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    if (!filter.trim()) return tables
    const q = filter.toLowerCase()
    return tables.filter((t) =>
      t.name.toLowerCase().includes(q) ||
      t.database.toLowerCase().includes(q) ||
      t.catalog_type.toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      t.full_name.toLowerCase().includes(q)
    )
  }, [tables, filter])

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Tables</h2>
        <p>Browse all tables in the data lake ontology</p>
      </div>

      <div className="search-bar">
        <input
          placeholder="Filter tables by name, database, type, description..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {filter && (
          <span style={{ fontSize: 12, color: 'var(--text-dim)', marginLeft: 8 }}>
            {filtered.length} of {tables.length} tables
          </span>
        )}
      </div>

      <div className="card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Database</th>
              <th>Type</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.full_name} onClick={() => window.location.href = `/tables/${t.full_name}`}>
                <td><Link to={`/tables/${t.full_name}`}>{t.name}</Link></td>
                <td><span className="tag tag-blue">{t.database}</span></td>
                <td><span className="tag tag-purple">{t.catalog_type}</span></td>
                <td style={{ color: 'var(--text-dim)', fontSize: 13 }}>{t.description}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={4} className="empty-state">{filter ? 'No tables match your filter.' : 'No tables found. Run a scan from the Admin page.'}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
