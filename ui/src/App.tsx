import { useEffect, useState, type ReactNode } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import { api } from './api'
import { isAuthEnabled, isAuthenticated, handleAuthCallback, getUserEmail, logout } from './auth'
import Dashboard from './pages/Dashboard'
import Tables from './pages/Tables'
import TableDetail from './pages/TableDetail'
import Metrics from './pages/Metrics'
import GraphExplorer from './pages/GraphExplorer'
import QueryBuilder from './pages/QueryBuilder'
import Documents from './pages/Documents'
import Admin from './pages/Admin'
import Datasources from './pages/Datasources'
import SimilarityExplorer from './pages/SimilarityExplorer'
import Login from './pages/Login'

function App() {
  const [neo4jStatus, setNeo4jStatus] = useState<'connected' | 'disconnected'>('disconnected')
  const [authed, setAuthed] = useState(() => isAuthenticated())
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    return (localStorage.getItem('theme') as 'light' | 'dark') || 'light'
  })
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebarCollapsed') === 'true')

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

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', String(collapsed))
  }, [collapsed])

  const toggleTheme = () => setTheme((t) => (t === 'light' ? 'dark' : 'light'))
  const toggleCollapsed = () => setCollapsed((c) => !c)

  // Show login page if auth is enabled and user is not authenticated
  if (isAuthEnabled() && !authed) {
    return <Login />
  }

  return (
    <div className={`app-layout${collapsed ? ' sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="sidebar-logo-text">
            <h1>Rosetta SDL</h1>
            <span>Translate business language into data insights</span>
          </div>
          <button
            className="sidebar-toggle"
            onClick={toggleCollapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? '\u00BB' : '\u00AB'}
          </button>
        </div>
        <nav>
          <NavItem to="/" end icon={icons.dashboard} label="Dashboard" collapsed={collapsed} />
          <NavItem to="/tables" icon={icons.tables} label="Tables" collapsed={collapsed} />
          <NavItem to="/documents" icon={icons.documents} label="Documents" collapsed={collapsed} />
          <NavItem to="/metrics" icon={icons.metrics} label="Metrics" collapsed={collapsed} />
          <NavItem to="/query-builder" icon={icons.query} label="Query Builder" collapsed={collapsed} />
          <NavItem to="/graph" icon={icons.graph} label="Graph Explorer" collapsed={collapsed} />
          <NavItem to="/datasources" icon={icons.datasources} label="Datasources" collapsed={collapsed} />
          <NavItem to="/similarity" icon={icons.similarity} label="Similarity Explorer" collapsed={collapsed} />
          <NavItem to="/admin" icon={icons.admin} label="Admin" collapsed={collapsed} />
        </nav>
        <div className="sidebar-footer">
          {isAuthEnabled() && (
            <div className="sidebar-user">
              <span title={getUserEmail() ?? undefined}>{getUserEmail()}</span>
              <button onClick={logout} className="sidebar-signout">
                Sign out
              </button>
            </div>
          )}
          <button className="theme-toggle" onClick={toggleTheme} title={theme === 'light' ? 'Dark mode' : 'Light mode'}>
            <span className="theme-toggle-icon">{theme === 'light' ? '\u263E' : '\u2600'}</span>
            <span className="nav-label">{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
          </button>
          <div className="sidebar-status" title={`Neo4j: ${neo4jStatus}`}>
            <span className={`status-dot ${neo4jStatus}`} />
            <span className="nav-label">Neo4j: {neo4jStatus}</span>
          </div>
        </div>
      </aside>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/tables" element={<Tables />} />
          <Route path="/tables/:name" element={<TableDetail />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/metrics" element={<Metrics />} />
          <Route path="/query-builder" element={<QueryBuilder />} />
          <Route path="/graph" element={<GraphExplorer />} />
          <Route path="/datasources" element={<Datasources />} />
          <Route path="/similarity" element={<SimilarityExplorer />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  )
}

function NavItem({
  to,
  icon,
  label,
  collapsed,
  end,
}: {
  to: string
  icon: ReactNode
  label: string
  collapsed: boolean
  end?: boolean
}) {
  return (
    <NavLink to={to} end={end} title={collapsed ? label : undefined}>
      <span className="nav-icon">{icon}</span>
      <span className="nav-label">{label}</span>
    </NavLink>
  )
}

const svg = (path: ReactNode) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {path}
  </svg>
)

const icons = {
  dashboard: svg(<><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></>),
  tables: svg(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" /></>),
  documents: svg(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>),
  metrics: svg(<><path d="M3 3v18h18" /><path d="M7 14l4-4 3 3 5-6" /></>),
  query: svg(<><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" /></>),
  graph: svg(<><circle cx="5" cy="6" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M7 7l3.5 8.5M17 7l-3.5 8.5M7 6h10" /></>),
  datasources: svg(<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" /><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" /></>),
  similarity: svg(<><circle cx="9" cy="12" r="6" /><circle cx="15" cy="12" r="6" /></>),
  admin: svg(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>),
}

export default App
