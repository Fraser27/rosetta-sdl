import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
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
  const [isFullscreen, setIsFullscreen] = useState(false)

  // Pan & zoom state
  const [zoom, setZoom] = useState(1)
  const [panX, setPanX] = useState(0)
  const [panY, setPanY] = useState(0)
  const isPanning = useRef(false)
  const panStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })

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

  // Fullscreen toggle
  const toggleFullscreen = useCallback(() => {
    setIsFullscreen((f) => !f)
  }, [])

  // Escape key exits fullscreen
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) setIsFullscreen(false)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isFullscreen])

  // Convert screen coords to world coords
  const screenToWorld = useCallback((sx: number, sy: number) => {
    return {
      x: (sx - panX) / zoom,
      y: (sy - panY) / zoom,
    }
  }, [panX, panY, zoom])

  // Zoom functions
  const handleZoomIn = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const cx = canvas.clientWidth / 2, cy = canvas.clientHeight / 2
    const newZoom = Math.min(zoom * 1.3, 5)
    setPanX(cx - (cx - panX) * (newZoom / zoom))
    setPanY(cy - (cy - panY) * (newZoom / zoom))
    setZoom(newZoom)
  }

  const handleZoomOut = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const cx = canvas.clientWidth / 2, cy = canvas.clientHeight / 2
    const newZoom = Math.max(zoom / 1.3, 0.1)
    setPanX(cx - (cx - panX) * (newZoom / zoom))
    setPanY(cy - (cy - panY) * (newZoom / zoom))
    setZoom(newZoom)
  }

  const handleFitToScreen = () => {
    if (nodes.length === 0) return
    const canvas = canvasRef.current
    if (!canvas) return
    const cw = canvas.clientWidth, ch = canvas.clientHeight
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (const n of nodes) {
      if (n.x < minX) minX = n.x
      if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y
      if (n.y > maxY) maxY = n.y
    }
    const graphW = maxX - minX || 100, graphH = maxY - minY || 100
    const pad = 80
    const newZoom = Math.min((cw - pad * 2) / graphW, (ch - pad * 2) / graphH, 3)
    const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2
    setZoom(newZoom)
    setPanX(cw / 2 - centerX * newZoom)
    setPanY(ch / 2 - centerY * newZoom)
  }

  const handleResetZoom = () => {
    setZoom(1)
    setPanX(0)
    setPanY(0)
  }

  // Wheel zoom
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left, my = e.clientY - rect.top
    const factor = e.deltaY < 0 ? 1.1 : 0.9
    const newZoom = Math.min(Math.max(zoom * factor, 0.1), 5)
    setPanX(mx - (mx - panX) * (newZoom / zoom))
    setPanY(my - (my - panY) * (newZoom / zoom))
    setZoom(newZoom)
  }, [zoom, panX, panY])

  // Build datasource membership from graph edges
  const nodeToDatasource = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of allNodes) {
      if (n.type === 'DataSource') map.set(n.id, n.label)
    }
    for (const e of allEdges) {
      if (e.type === 'CONTAINS' && map.has(e.source)) map.set(e.target, map.get(e.source)!)
    }
    for (const e of allEdges) {
      if (e.type === 'HAS_COLUMN' && map.has(e.source)) map.set(e.target, map.get(e.source)!)
    }
    for (const e of allEdges) {
      if (e.type === 'MEASURES' && map.has(e.target)) map.set(e.source, map.get(e.target)!)
    }
    for (const e of allEdges) {
      if (e.type === 'HAS_METADATA_KEY' && map.has(e.source)) map.set(e.target, map.get(e.source)!)
    }
    return map
  }, [allNodes, allEdges])

  const datasources = useMemo(() => {
    return Array.from(new Set(nodeToDatasource.values())).sort()
  }, [nodeToDatasource])

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

  useEffect(() => { setSelectedTable('__all__') }, [selectedDatasource])

  const nodeTypes = useMemo(() => {
    const types = new Set<string>()
    for (const n of allNodes) types.add(n.type)
    return Array.from(types).sort()
  }, [allNodes])

  const { nodes, edges } = useMemo(() => {
    if (selectedTable !== '__all__') {
      const tableNode = allNodes.find((n) => n.type === 'Table' && n.label === selectedTable)
      if (!tableNode) return { nodes: [], edges: [] }
      const connectedIds = new Set<string>([tableNode.id])
      const tableEdges: GraphEdge[] = []
      for (const e of allEdges) {
        if (e.source === tableNode.id) { connectedIds.add(e.target); tableEdges.push(e) }
        if (e.target === tableNode.id) { connectedIds.add(e.source); tableEdges.push(e) }
      }
      const filteredNodes = allNodes.filter((n) => connectedIds.has(n.id) && visibleTypes.has(n.type))
      const visibleIds = new Set(filteredNodes.map((n) => n.id))
      const filteredEdges = tableEdges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
      return { nodes: filteredNodes, edges: filteredEdges }
    }
    const filteredNodes = allNodes.filter((n) => {
      if (!visibleTypes.has(n.type)) return false
      if (selectedDatasource === '__all__') return true
      const ds = nodeToDatasource.get(n.id)
      if (ds) return ds === selectedDatasource
      return true
    })
    const visibleIds = new Set(filteredNodes.map((n) => n.id))
    const filteredEdges = allEdges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
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
      const cw = (canvas?.clientWidth || 800) / zoom
      const ch = (canvas?.clientHeight || 500) / zoom

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
    ctx.save()
    ctx.translate(panX, panY)
    ctx.scale(zoom, zoom)

    const style = getComputedStyle(document.documentElement)
    const labelColor = style.getPropertyValue('--text').trim() || '#1a1d2e'
    const dimColor = style.getPropertyValue('--text-dim').trim() || '#6b7085'
    const edgeColor = style.getPropertyValue('--graph-edge').trim() || 'rgba(45, 49, 72, 0.6)'

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    ctx.strokeStyle = edgeColor
    ctx.lineWidth = 1 / zoom
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
        ctx.lineWidth = 2 / zoom
        ctx.stroke()
      }

      ctx.fillStyle = labelColor
      ctx.font = n.type === 'Column' ? '9px Inter, sans-serif' : '11px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(n.label, n.x, n.y + radius + 14)
    }

    // Tooltip (drawn in world coords)
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
      ctx.lineWidth = 1 / zoom
      ctx.stroke()
      ctx.fillStyle = labelColor
      ctx.textAlign = 'left'
      for (let i = 0; i < lines.length; i++) {
        ctx.fillText(lines[i], x + 10, y + 3 + i * 18)
      }
    }

    ctx.restore()
  }

  // Redraw when pan/zoom changes
  useEffect(() => { draw() }, [panX, panY, zoom])

  const getNodeAt = (sx: number, sy: number): SimNode | null => {
    const { x: mx, y: my } = screenToWorld(sx, sy)
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
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top
    const node = getNodeAt(sx, sy)
    if (node) {
      dragRef.current = node
    } else {
      // Start panning
      isPanning.current = true
      panStart.current = { x: e.clientX, y: e.clientY, panX, panY }
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top

    if (isPanning.current) {
      setPanX(panStart.current.panX + (e.clientX - panStart.current.x))
      setPanY(panStart.current.panY + (e.clientY - panStart.current.y))
      return
    }

    if (dragRef.current) {
      const { x, y } = screenToWorld(sx, sy)
      dragRef.current.x = x
      dragRef.current.y = y
      dragRef.current.vx = 0
      dragRef.current.vy = 0
    } else {
      setHovered(getNodeAt(sx, sy))
    }
  }

  const handleMouseUp = () => {
    dragRef.current = null
    isPanning.current = false
  }

  const toggleType = (type: string) => {
    setVisibleTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const filterDesc = useMemo(() => {
    const parts: string[] = []
    if (selectedDatasource !== '__all__') parts.push(selectedDatasource)
    if (selectedTable !== '__all__') parts.push(selectedTable)
    return parts.length ? ` (filtered to ${parts.join(' / ')})` : ''
  }, [selectedDatasource, selectedTable])

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error loading graph: {error}</div>

  return (
    <div className={`graph-page ${isFullscreen ? 'graph-fullscreen' : ''}`}>
      {/* Toolbar */}
      <div className="graph-toolbar">
        <div className="graph-toolbar-left">
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
                  style={{ '--toggle-color': NODE_COLORS[type] || '#6c8cff' } as React.CSSProperties}
                  onClick={() => toggleType(type)}
                >
                  <span
                    className="graph-legend-dot"
                    style={{ background: visibleTypes.has(type) ? (NODE_COLORS[type] || '#6c8cff') : 'var(--border)' }}
                  />
                  {type}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="graph-toolbar-right">
          <span className="graph-stats">
            {nodes.length} nodes, {edges.length} edges{filterDesc}
          </span>
        </div>
      </div>

      {/* Canvas */}
      <div className="graph-container graph-container-fill" ref={containerRef}>
        <canvas
          ref={canvasRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          style={{ cursor: isPanning.current ? 'grabbing' : dragRef.current ? 'grabbing' : hovered ? 'grab' : 'default' }}
        />

        {/* Zoom controls */}
        <div className="graph-zoom-controls">
          <button onClick={handleZoomIn} title="Zoom in">+</button>
          <button onClick={handleZoomOut} title="Zoom out">&minus;</button>
          <button onClick={handleFitToScreen} title="Fit to screen">&#x2922;</button>
          <button onClick={handleResetZoom} title="Reset zoom">1:1</button>
          <button onClick={toggleFullscreen} title={isFullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}>
            {isFullscreen ? '\u2716' : '\u26F6'}
          </button>
        </div>

        {/* Zoom indicator */}
        <div className="graph-zoom-indicator">{Math.round(zoom * 100)}%</div>
      </div>
    </div>
  )
}
