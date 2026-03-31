import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type TableSummary, type SearchResult } from '../api'

export default function Tables() {
  const [tables, setTables] = useState<TableSummary[]>([])
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listTables()
      .then(setTables)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const handleSearch = async (q: string) => {
    setQuery(q)
    if (q.length < 2) { setSearchResults(null); return }
    try {
      const results = await api.search(q)
      setSearchResults(results)
    } catch { setSearchResults(null) }
  }

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Tables</h2>
        <p>Browse all tables in the data lake ontology</p>
      </div>

      <div className="search-bar">
        <input
          placeholder="Search tables, metrics, documents..."
          value={query}
          onChange={(e) => handleSearch(e.target.value)}
        />
      </div>

      {searchResults ? (
        <div className="card">
          <div className="card-header">
            <h3>Search Results ({searchResults.length})</h3>
            <button className="btn btn-ghost btn-sm" onClick={() => { setQuery(''); setSearchResults(null) }}>Clear</button>
          </div>
          <table className="data-table">
            <thead>
              <tr><th>Type</th><th>Name</th><th>Description</th><th>Score</th></tr>
            </thead>
            <tbody>
              {searchResults.map((r) => (
                <tr key={r.id}>
                  <td><span className={`tag ${r.type === 'table' ? 'tag-blue' : r.type === 'metric' ? 'tag-green' : 'tag-purple'}`}>{r.type}</span></td>
                  <td>{r.type === 'table' ? <Link to={`/tables/${r.id}`}>{r.name}</Link> : r.name}</td>
                  <td style={{ color: 'var(--text-dim)', fontSize: 13 }}>{r.description}</td>
                  <td>{r.score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
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
              {tables.map((t) => (
                <tr key={t.full_name} onClick={() => window.location.href = `/tables/${t.full_name}`}>
                  <td><Link to={`/tables/${t.full_name}`}>{t.name}</Link></td>
                  <td><span className="tag tag-blue">{t.database}</span></td>
                  <td><span className="tag tag-purple">{t.catalog_type}</span></td>
                  <td style={{ color: 'var(--text-dim)', fontSize: 13 }}>{t.description}</td>
                </tr>
              ))}
              {tables.length === 0 && (
                <tr><td colSpan={4} className="empty-state">No tables found. Run a scan from the Admin page.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
