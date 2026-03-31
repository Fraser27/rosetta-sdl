import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type GraphSummary, type TableSummary, type Metric } from '../api'

export default function Dashboard() {
  const [graph, setGraph] = useState<GraphSummary | null>(null)
  const [tables, setTables] = useState<TableSummary[]>([])
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.graphSummary(), api.listTables(), api.listMetrics()])
      .then(([g, t, m]) => { setGraph(g); setTables(t); setMetrics(m) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>Overview of your semantic layer ontology</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">Tables</div>
          <div className="value accent">{tables.length}</div>
        </div>
        <div className="stat-card">
          <div className="label">Metrics</div>
          <div className="value green">{metrics.length}</div>
        </div>
        {graph && Object.entries(graph.nodes).map(([label, count]) => (
          <div className="stat-card" key={label}>
            <div className="label">{label}</div>
            <div className="value purple">{count}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Tables</h3>
          <Link to="/tables" className="btn btn-ghost btn-sm">View all</Link>
        </div>
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
            {tables.slice(0, 5).map((t) => (
              <tr key={t.full_name} onClick={() => window.location.href = `/tables/${t.full_name}`}>
                <td><Link to={`/tables/${t.full_name}`}>{t.name}</Link></td>
                <td><span className="tag tag-blue">{t.database}</span></td>
                <td><span className="tag tag-purple">{t.catalog_type}</span></td>
                <td style={{ color: 'var(--text-dim)', fontSize: 13 }}>{t.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-header">
          <h3>Metrics</h3>
          <Link to="/metrics" className="btn btn-ghost btn-sm">View all</Link>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Expression</th>
              <th>Source Table</th>
              <th>Type</th>
            </tr>
          </thead>
          <tbody>
            {metrics.slice(0, 5).map((m) => (
              <tr key={m.metric_id}>
                <td>{m.name}</td>
                <td><code style={{ fontSize: 12 }}>{m.expression}</code></td>
                <td><span className="tag tag-blue">{m.source_table}</span></td>
                <td><span className="tag tag-green">{m.type}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
