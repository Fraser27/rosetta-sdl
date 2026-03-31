import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, type TableDetail as TableDetailType } from '../api'

export default function TableDetail() {
  const { name } = useParams<{ name: string }>()
  const [table, setTable] = useState<TableDetailType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!name) return
    api.getTable(name)
      .then(setTable)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [name])

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error: {error}</div>
  if (!table) return <div className="empty-state">Table not found</div>

  return (
    <>
      <Link to="/tables" className="back-link">&larr; Back to Tables</Link>

      <div className="page-header">
        <h2>{table.name}</h2>
        <p>{table.description || table.full_name}</p>
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
            </tr>
          </thead>
          <tbody>
            {table.columns.map((c) => (
              <tr key={c.name}>
                <td><strong>{c.name}</strong></td>
                <td><code style={{ fontSize: 12 }}>{c.data_type}</code></td>
                <td style={{ color: 'var(--text-dim)', fontSize: 13 }}>{c.description}</td>
                <td>{c.is_partition && <span className="tag tag-orange">partition</span>}</td>
                <td>{c.is_primary_key && <span className="tag tag-green">PK</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
