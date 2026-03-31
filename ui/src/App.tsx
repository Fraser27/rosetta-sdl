import { useEffect, useState } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import { api } from './api'
import { isAuthEnabled, isAuthenticated, handleAuthCallback, getUserEmail, logout } from './auth'
import Dashboard from './pages/Dashboard'
import Tables from './pages/Tables'
import TableDetail from './pages/TableDetail'
import Metrics from './pages/Metrics'
import GraphExplorer from './pages/GraphExplorer'
import Admin from './pages/Admin'
import Login from './pages/Login'

function App() {
  const [neo4jStatus, setNeo4jStatus] = useState<'connected' | 'disconnected'>('disconnected')
  const [authed, setAuthed] = useState(() => isAuthenticated())
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    return (localStorage.getItem('theme') as 'light' | 'dark') || 'light'
  })

  // Handle Cognito callback (tokens in URL hash)
  useEffect(() => {
    if (handleAuthCallback()) {
      setAuthed(true)
    }
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    if (!authed) return
    api.health()
      .then((h) => setNeo4jStatus(h.neo4j === 'connected' ? 'connected' : 'disconnected'))
      .catch(() => setNeo4jStatus('disconnected'))
  }, [authed])

  const toggleTheme = () => setTheme((t) => (t === 'light' ? 'dark' : 'light'))

  // Show login page if auth is enabled and user is not authenticated
  if (isAuthEnabled() && !authed) {
    return <Login />
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>Rosetta SDL</h1>
          <span>Translate business language into data insights</span>
        </div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/tables">Tables</NavLink>
          <NavLink to="/metrics">Metrics</NavLink>
          <NavLink to="/graph">Graph Explorer</NavLink>
          <NavLink to="/admin">Admin</NavLink>
        </nav>
        <div className="sidebar-footer">
          {isAuthEnabled() && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: 'var(--text-dim)', marginBottom: 6 }}>
              <span>{getUserEmail()}</span>
              <button
                onClick={logout}
                style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', fontSize: 12 }}
              >
                Sign out
              </button>
            </div>
          )}
          <button className="theme-toggle" onClick={toggleTheme}>
            {theme === 'light' ? '\u263E' : '\u2600'} {theme === 'light' ? 'Dark mode' : 'Light mode'}
          </button>
          <div className="sidebar-status">
            <span className={`status-dot ${neo4jStatus}`} />
            Neo4j: {neo4jStatus}
          </div>
        </div>
      </aside>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/tables" element={<Tables />} />
          <Route path="/tables/:name" element={<TableDetail />} />
          <Route path="/metrics" element={<Metrics />} />
          <Route path="/graph" element={<GraphExplorer />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
