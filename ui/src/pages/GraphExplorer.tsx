import { useEffect, useRef, useState, useMemo } from 'react'
import { api, type GraphNode, type GraphEdge } from '../api'

const NODE_COLORS: Record<string, string> = {
  Table: '#6c8cff',
  Column: '#8b90a5',
  Metric: '#4ade80',
  BusinessTerm: '#fbbf24',
  DataSource: '#a78bfa',
  Document: '#f87171',
  Concept: '#fb923c',
  MetadataKey: '#38bdf8',
}

interface SimNode extends GraphNode {
  x: number
  y: number
  vx: number
  vy: number
}

export default function GraphExplorer() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [allNodes, setAllNodes] = useState<SimNode[]>([])
  const [allEdges, setAllEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hovered, setHovered] = useState<SimNode | null>(null)
  const dragRef = useRef<SimNode | null>(null)
  const animRef = useRef<number>(0)

  // Filters
  const [selectedDatasource, setSelectedDatasource] = useState<string>('__all__')
  const [selectedTable, setSelectedTable] = useState<string>('__all__')
  const [visibleTypes, setVisibleTypes] = useState<Set<string>>(new Set(Object.keys(NODE_COLORS)))

  useEffect(() => {
    api.graphData()
      .then(({ nodes: n, edges: e }) => {
        const simNodes: SimNode[] = n.map((node, i) => ({
          ...node,
          x: 400 + Math.cos(i * 0.6) * (200 + Math.random() * 150),
          y: 250 + Math.sin(i * 0.6) * (150 + Math.random() * 120),
          vx: 0,
          vy: 0,
        }))
        setAllNodes(simNodes)
        setAllEdges(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Build datasource membership from graph edges (more reliable than Cypher properties)
  const nodeToDatasource = useMemo(() => {
    const map = new Map<string, string>()

    // DataSource nodes own themselves
    for (const n of allNodes) {
      if (n.type === 'DataSource') map.set(n.id, n.label)
    }

    // CONTAINS: DataSource -> Table
    for (const e of allEdges) {
      if (e.type === 'CONTAINS' && map.has(e.source)) {
        map.set(e.target, map.get(e.source)!)
      }
    }

    // HAS_COLUMN: Table -> Column (inherit datasource from parent table)
    for (const e of allEdges) {
      if (e.type === 'HAS_COLUMN' && map.has(e.source)) {
        map.set(e.target, map.get(e.source)!)
      }
    }

    // MEASURES: Metric -> Table (inherit datasource from measured table)
    for (const e of allEdges) {
      if (e.type === 'MEASURES' && map.has(e.target)) {
        map.set(e.source, map.get(e.target)!)
      }
    }

    // HAS_METADATA_KEY: Document -> MetadataKey
    for (const e of allEdges) {
      if (e.type === 'HAS_METADATA_KEY' && map.has(e.source)) {
        map.set(e.target, map.get(e.source)!)
      }
    }

    return map
  }, [allNodes, allEdges])

  // Extract unique datasources
  const datasources = useMemo(() => {
    return Array.from(new Set(nodeToDatasource.values())).sort()
  }, [nodeToDatasource])

  // Extract tables for the selected datasource
  const tables = useMemo(() => {
    return allNodes
      .filter((n) => {
        if (n.type !== 'Table') return false
        if (selectedDatasource === '__all__') return true
        return nodeToDatasource.get(n.id) === selectedDatasource
      })
      .map((n) => n.label)
      .sort()
  }, [allNodes, selectedDatasource, nodeToDatasource])

  // Reset table filter when datasource changes
  useEffect(() => {
    setSelectedTable('__all__')
  }, [selectedDatasource])

  // Extract node types present in the data
  const nodeTypes = useMemo(() => {
    const types = new Set<string>()
    for (const n of allNodes) types.add(n.type)
    return Array.from(types).sort()
  }, [allNodes])

  // Filtered nodes and edges
  const { nodes, edges } = useMemo(() => {
    // When a specific table is selected, show it + all directly connected nodes
    if (selectedTable !== '__all__') {
      const tableNode = allNodes.find(
        (n) => n.type === 'Table' && n.label === selectedTable
      )
      if (!tableNode) return { nodes: [], edges: [] }

      // Find all nodes connected to this table (1-hop neighborhood)
      const connectedIds = new Set<string>([tableNode.id])
      const tableEdges: GraphEdge[] = []
      for (const e of allEdges) {
        if (e.source === tableNode.id) {
          connectedIds.add(e.target)
          tableEdges.push(e)
        }
        if (e.target === tableNode.id) {
          connectedIds.add(e.source)
          tableEdges.push(e)
        }
      }

      // Also include edges between the connected nodes (e.g. JOINS_TO targets' columns)
      // Keep it to 1-hop for clarity

      const filteredNodes = allNodes.filter(
        (n) => connectedIds.has(n.id) && visibleTypes.has(n.type)
      )
      const visibleIds = new Set(filteredNodes.map((n) => n.id))
      const filteredEdges = tableEdges.filter(
        (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
      )

      return { nodes: filteredNodes, edges: filteredEdges }
    }

    // Datasource + type filter
    const filteredNodes = allNodes.filter((n) => {
      if (!visibleTypes.has(n.type)) return false
      if (selectedDatasource === '__all__') return true
      const ds = nodeToDatasource.get(n.id)
      if (ds) return ds === selectedDatasource
      // Nodes without a datasource (e.g. BusinessTerm, Concept): show always
      return true
    })

    const visibleIds = new Set(filteredNodes.map((n) => n.id))
    const filteredEdges = allEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
    )

    return { nodes: filteredNodes, edges: filteredEdges }
  }, [allNodes, allEdges, selectedDatasource, selectedTable, visibleTypes, nodeToDatasource])

  // Re-scatter positions when filter changes significantly
  const prevCountRef = useRef(0)
  useEffect(() => {
    if (nodes.length > 0 && Math.abs(nodes.length - prevCountRef.current) > 5) {
      const canvas = canvasRef.current
      const cw = canvas?.clientWidth || 800
      const ch = canvas?.clientHeight || 500
      for (let i = 0; i < nodes.length; i++) {
        nodes[i].x = cw / 2 + Math.cos(i * 0.6) * (180 + Math.random() * 140)
        nodes[i].y = ch / 2 + Math.sin(i * 0.6) * (130 + Math.random() * 110)
        nodes[i].vx = 0
        nodes[i].vy = 0
      }
    }
    prevCountRef.current = nodes.length
  }, [nodes.length])

  // Force simulation
  useEffect(() => {
    if (nodes.length === 0) return

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    const neighbors = new Map<string, Set<string>>()
    for (const n of nodes) neighbors.set(n.id, new Set())
    for (const e of edges) {
      neighbors.get(e.source)?.add(e.target)
      neighbors.get(e.target)?.add(e.source)
    }

    let alpha = 1

    const tick = () => {
      alpha *= 0.995
      if (alpha < 0.001) alpha = 0.001

      const canvas = canvasRef.current
      const cw = canvas?.clientWidth || 800
      const ch = canvas?.clientHeight || 500
      const pad = 30

      for (const n of nodes) {
        n.vx += (cw / 2 - n.x) * 0.0004 * alpha
        n.vy += (ch / 2 - n.y) * 0.0004 * alpha
      }

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j]
          const dx = b.x - a.x, dy = b.y - a.y
          const distSq = dx * dx + dy * dy || 1
          const dist = Math.sqrt(distSq)
          if (dist < 350) {
            const force = 600 * alpha / distSq
            a.vx -= dx * force; a.vy -= dy * force
            b.vx += dx * force; b.vy += dy * force
          }
        }
      }

      for (const e of edges) {
        const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const force = (dist - 140) * 0.004 * alpha / dist
        a.vx += dx * force; a.vy += dy * force
        b.vx -= dx * force; b.vy -= dy * force
      }

      if (dragRef.current) {
        const dragged = dragRef.current
        const nbs = neighbors.get(dragged.id)
        if (nbs) {
          for (const nbId of nbs) {
            const nb = nodeMap.get(nbId)
            if (!nb) continue
            const dx = dragged.x - nb.x, dy = dragged.y - nb.y
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            if (dist > 180) {
              const pull = (dist - 180) * 0.008 / dist
              nb.vx += dx * pull
              nb.vy += dy * pull
            }
          }
        }
      }

      for (const n of nodes) {
        if (n === dragRef.current) continue
        n.vx *= 0.9; n.vy *= 0.9
        n.x += n.vx; n.y += n.vy
        if (n.x < pad) { n.x = pad; n.vx *= -0.5; }
        if (n.x > cw - pad) { n.x = cw - pad; n.vx *= -0.5; }
        if (n.y < pad) { n.y = pad; n.vy *= -0.5; }
        if (n.y > ch - pad) { n.y = ch - pad; n.vy *= -0.5; }
      }

      draw()
      animRef.current = requestAnimationFrame(tick)
    }

    animRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(animRef.current)
  }, [nodes, edges])

  const draw = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const container = containerRef.current
    if (container) {
      canvas.width = container.clientWidth
      canvas.height = container.clientHeight
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const style = getComputedStyle(document.documentElement)
    const labelColor = style.getPropertyValue('--text').trim() || '#1a1d2e'
    const dimColor = style.getPropertyValue('--text-dim').trim() || '#6b7085'
    const edgeColor = style.getPropertyValue('--graph-edge').trim() || 'rgba(45, 49, 72, 0.6)'

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    ctx.strokeStyle = edgeColor
    ctx.lineWidth = 1
    for (const e of edges) {
      const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
      if (!a || !b) continue
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()

      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
      ctx.fillStyle = dimColor
      ctx.font = '9px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(e.type, mx, my - 4)
    }

    for (const n of nodes) {
      const color = NODE_COLORS[n.type] || '#6c8cff'
      const radius = n.type === 'Table' || n.type === 'DataSource' ? 12 :
                     n.type === 'Metric' ? 10 :
                     n.type === 'Column' ? 5 : 8

      ctx.beginPath()
      ctx.arc(n.x, n.y, radius, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()

      if (n === hovered) {
        ctx.strokeStyle = labelColor
        ctx.lineWidth = 2
        ctx.stroke()
      }

      ctx.fillStyle = labelColor
      ctx.font = n.type === 'Column' ? '9px Inter, sans-serif' : '11px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(n.label, n.x, n.y + radius + 14)
    }

    if (hovered) {
      const x = hovered.x + 16, y = hovered.y - 10
      const lines = [`${hovered.type}: ${hovered.label}`]
      if (hovered.datasource) lines.push(`Source: ${hovered.datasource}`)
      ctx.font = '12px Inter, sans-serif'
      const maxW = Math.max(...lines.map((l) => ctx.measureText(l).width))
      const w = maxW + 20
      const h = lines.length * 18 + 10
      const tooltipBg = style.getPropertyValue('--bg-card').trim() || 'rgba(26, 29, 39, 0.95)'
      ctx.fillStyle = tooltipBg
      ctx.beginPath()
      ctx.roundRect(x, y - 14, w, h, 4)
      ctx.fill()
      ctx.strokeStyle = edgeColor
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = labelColor
      ctx.textAlign = 'left'
      for (let i = 0; i < lines.length; i++) {
        ctx.fillText(lines[i], x + 10, y + 3 + i * 18)
      }
    }
  }

  const getNodeAt = (mx: number, my: number): SimNode | null => {
    for (const n of [...nodes].reverse()) {
      const r = n.type === 'Column' ? 5 : 12
      const dx = mx - n.x, dy = my - n.y
      if (dx * dx + dy * dy < r * r * 4) return n
    }
    return null
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const node = getNodeAt(e.clientX - rect.left, e.clientY - rect.top)
    if (node) dragRef.current = node
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left, my = e.clientY - rect.top

    if (dragRef.current) {
      dragRef.current.x = mx
      dragRef.current.y = my
      dragRef.current.vx = 0
      dragRef.current.vy = 0
    } else {
      setHovered(getNodeAt(mx, my))
    }
  }

  const handleMouseUp = () => { dragRef.current = null }

  const toggleType = (type: string) => {
    setVisibleTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }

  // Build filter description for stats
  const filterDesc = useMemo(() => {
    const parts: string[] = []
    if (selectedDatasource !== '__all__') parts.push(selectedDatasource)
    if (selectedTable !== '__all__') parts.push(selectedTable)
    return parts.length ? ` (filtered to ${parts.join(' / ')})` : ''
  }, [selectedDatasource, selectedTable])

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error loading graph: {error}</div>

  return (
    <>
      <div className="page-header">
        <h2>Graph Explorer</h2>
        <p>Interactive visualization of the semantic layer ontology</p>
      </div>

      <div className="graph-filters">
        <div className="graph-filter-group">
          <label className="graph-filter-label">DataSource</label>
          <select
            className="graph-filter-select"
            value={selectedDatasource}
            onChange={(e) => setSelectedDatasource(e.target.value)}
          >
            <option value="__all__">All DataSources</option>
            {datasources.map((ds) => (
              <option key={ds} value={ds}>{ds}</option>
            ))}
          </select>
        </div>

        <div className="graph-filter-group">
          <label className="graph-filter-label">Table</label>
          <select
            className="graph-filter-select"
            value={selectedTable}
            onChange={(e) => setSelectedTable(e.target.value)}
          >
            <option value="__all__">All Tables</option>
            {tables.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <div className="graph-filter-group">
          <label className="graph-filter-label">Node Types</label>
          <div className="graph-type-toggles">
            {nodeTypes.map((type) => (
              <button
                key={type}
                className={`graph-type-toggle ${visibleTypes.has(type) ? 'active' : ''}`}
                style={{
                  '--toggle-color': NODE_COLORS[type] || '#6c8cff',
                } as React.CSSProperties}
                onClick={() => toggleType(type)}
              >
                <span
                  className="graph-legend-dot"
                  style={{
                    background: visibleTypes.has(type)
                      ? (NODE_COLORS[type] || '#6c8cff')
                      : 'var(--border)',
                  }}
                />
                {type}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="graph-container" ref={containerRef}>
        <canvas
          ref={canvasRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          style={{ cursor: dragRef.current ? 'grabbing' : hovered ? 'grab' : 'default' }}
        />
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-header">
          <h3>Graph Stats</h3>
        </div>
        <p style={{ fontSize: 14, color: 'var(--text-dim)' }}>
          Showing {nodes.length} nodes, {edges.length} edges{filterDesc}.
          {allNodes.length !== nodes.length && ` Total: ${allNodes.length} nodes, ${allEdges.length} edges.`}
          {' '}Drag nodes to rearrange.
        </p>
      </div>
    </>
  )
}
