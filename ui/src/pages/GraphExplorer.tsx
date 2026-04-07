import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
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

const NODE_RADIUS: Record<string, number> = {
  Table: 14,
  DataSource: 14,
  Metric: 12,
  Column: 6,
  BusinessTerm: 10,
  Document: 10,
  Concept: 9,
  MetadataKey: 7,
}

interface SimNode extends GraphNode {
  x: number
  y: number
  vx: number
  vy: number
  pinned?: boolean
}

/** BFS walk from seed IDs through edges, up to maxDepth hops. */
function bfsConnected(seedIds: Set<string>, allEdges: GraphEdge[], maxDepth: number): Set<string> {
  const visited = new Set(seedIds)
  let frontier = new Set(seedIds)
  for (let d = 0; d < maxDepth; d++) {
    const next = new Set<string>()
    for (const e of allEdges) {
      if (frontier.has(e.source) && !visited.has(e.target)) { next.add(e.target); visited.add(e.target) }
      if (frontier.has(e.target) && !visited.has(e.source)) { next.add(e.source); visited.add(e.source) }
    }
    if (next.size === 0) break
    frontier = next
  }
  return visited
}

export default function GraphExplorer() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const nodesRef = useRef<SimNode[]>([])
  const [allNodes, setAllNodes] = useState<SimNode[]>([])
  const [allEdges, setAllEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const dragRef = useRef<string | null>(null)
  const animRef = useRef<number>(0)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const alphaRef = useRef(1)
  const filterKeyRef = useRef(0)

  // Pan & zoom — stored in refs for real-time perf, state for UI
  const [zoom, setZoom] = useState(1)
  const [panX, setPanX] = useState(0)
  const [panY, setPanY] = useState(0)
  const zoomRef = useRef(1)
  const panXRef = useRef(0)
  const panYRef = useRef(0)
  useEffect(() => { zoomRef.current = zoom }, [zoom])
  useEffect(() => { panXRef.current = panX }, [panX])
  useEffect(() => { panYRef.current = panY }, [panY])
  const isPanning = useRef(false)
  const panStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })

  // URL params for deep-linking (e.g., /graph?metric=m_009)
  const [searchParams, setSearchParams] = useSearchParams()

  // Filters
  const [selectedDatasource, setSelectedDatasource] = useState<string>('__all__')
  const [selectedTable, setSelectedTable] = useState<string>('__all__')
  const [selectedMetric, setSelectedMetric] = useState<string>('__all__')
  const [visibleTypes, setVisibleTypes] = useState<Set<string>>(new Set(Object.keys(NODE_COLORS)))

  // Init metric from URL
  useEffect(() => {
    const m = searchParams.get('metric')
    if (m) setSelectedMetric(m)
  }, [])

  useEffect(() => {
    api.graphData()
      .then(({ nodes: n, edges: e }) => {
        const canvas = canvasRef.current
        const cw = canvas?.clientWidth || 800
        const ch = canvas?.clientHeight || 500
        const simNodes: SimNode[] = n.map((node, i) => ({
          ...node,
          x: cw / 2 + Math.cos(i * 0.7) * (200 + Math.random() * 150),
          y: ch / 2 + Math.sin(i * 0.7) * (150 + Math.random() * 120),
          vx: 0,
          vy: 0,
        }))
        nodesRef.current = simNodes
        setAllNodes(simNodes)
        setAllEdges(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Fullscreen
  const toggleFullscreen = useCallback(() => setIsFullscreen((f) => !f), [])
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) setIsFullscreen(false)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isFullscreen])

  const screenToWorld = useCallback((sx: number, sy: number) => ({
    x: (sx - panXRef.current) / zoomRef.current,
    y: (sy - panYRef.current) / zoomRef.current,
  }), [])

  // Zoom
  const applyZoom = useCallback((newZoom: number, cx: number, cy: number) => {
    const clamped = Math.min(Math.max(newZoom, 0.1), 5)
    const oldZoom = zoomRef.current
    const npx = cx - (cx - panXRef.current) * (clamped / oldZoom)
    const npy = cy - (cy - panYRef.current) * (clamped / oldZoom)
    setZoom(clamped); setPanX(npx); setPanY(npy)
  }, [])

  const handleZoomIn = () => {
    const c = canvasRef.current; if (!c) return
    applyZoom(zoomRef.current * 1.3, c.clientWidth / 2, c.clientHeight / 2)
  }
  const handleZoomOut = () => {
    const c = canvasRef.current; if (!c) return
    applyZoom(zoomRef.current / 1.3, c.clientWidth / 2, c.clientHeight / 2)
  }
  const handleResetZoom = () => { setZoom(1); setPanX(0); setPanY(0) }

  const fitToNodes = useCallback((targetNodes: SimNode[]) => {
    if (targetNodes.length === 0) return
    const canvas = canvasRef.current; if (!canvas) return
    const cw = canvas.clientWidth, ch = canvas.clientHeight
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (const n of targetNodes) {
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y
    }
    const graphW = maxX - minX || 100, graphH = maxY - minY || 100
    const pad = 100
    const newZoom = Math.min((cw - pad * 2) / graphW, (ch - pad * 2) / graphH, 2.5)
    const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2
    setZoom(newZoom); setPanX(cw / 2 - centerX * newZoom); setPanY(ch / 2 - centerY * newZoom)
  }, [])

  const handleFitToScreen = () => fitToNodes(nodesRef.current.filter((n) => visibleNodeIds.has(n.id)))

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const rect = canvasRef.current?.getBoundingClientRect(); if (!rect) return
    const factor = e.deltaY < 0 ? 1.1 : 0.9
    applyZoom(zoomRef.current * factor, e.clientX - rect.left, e.clientY - rect.top)
  }, [applyZoom])

  // Build datasource membership
  const nodeToDatasource = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of allNodes) { if (n.type === 'DataSource') map.set(n.id, n.label) }
    for (const e of allEdges) { if (e.type === 'CONTAINS' && map.has(e.source)) map.set(e.target, map.get(e.source)!) }
    for (const e of allEdges) { if (e.type === 'HAS_COLUMN' && map.has(e.source)) map.set(e.target, map.get(e.source)!) }
    for (const e of allEdges) { if (e.type === 'MEASURES' && map.has(e.target)) map.set(e.source, map.get(e.target)!) }
    for (const e of allEdges) { if (e.type === 'HAS_METADATA_KEY' && map.has(e.source)) map.set(e.target, map.get(e.source)!) }
    return map
  }, [allNodes, allEdges])

  const datasources = useMemo(() => Array.from(new Set(nodeToDatasource.values())).sort(), [nodeToDatasource])

  const metricNodes = useMemo(() =>
    allNodes.filter((n) => n.type === 'Metric').sort((a, b) => a.label.localeCompare(b.label)),
    [allNodes],
  )

  const tables = useMemo(() =>
    allNodes
      .filter((n) => {
        if (n.type !== 'Table') return false
        if (selectedDatasource === '__all__') return true
        return nodeToDatasource.get(n.id) === selectedDatasource
      })
      .map((n) => n.label).sort(),
    [allNodes, selectedDatasource, nodeToDatasource],
  )

  // Reset table when datasource changes
  useEffect(() => { setSelectedTable('__all__') }, [selectedDatasource])
  // Reset table/datasource when metric selected
  useEffect(() => {
    if (selectedMetric !== '__all__') {
      setSelectedTable('__all__')
      setSelectedDatasource('__all__')
    }
  }, [selectedMetric])

  const nodeTypes = useMemo(() => {
    const types = new Set<string>()
    for (const n of allNodes) types.add(n.type)
    return Array.from(types).sort()
  }, [allNodes])

  // Filtered node IDs — the core filter logic
  const visibleNodeIds = useMemo(() => {
    // Metric filter: BFS from metric node (2 hops to get metric→table→columns, metric→businessterm, etc.)
    if (selectedMetric !== '__all__') {
      const metricNode = allNodes.find((n) => n.type === 'Metric' && n.label === selectedMetric)
      if (!metricNode) return new Set<string>()
      const connected = bfsConnected(new Set([metricNode.id]), allEdges, 2)
      // Also include DataSource for any connected tables
      for (const id of [...connected]) {
        const ds = nodeToDatasource.get(id)
        if (ds) {
          const dsNode = allNodes.find((n) => n.type === 'DataSource' && n.label === ds)
          if (dsNode) connected.add(dsNode.id)
        }
      }
      return new Set([...connected].filter((id) => {
        const n = allNodes.find((n) => n.id === id)
        return n && visibleTypes.has(n.type)
      }))
    }

    // Table filter: 1-hop neighbors
    if (selectedTable !== '__all__') {
      const tableNode = allNodes.find((n) => n.type === 'Table' && n.label === selectedTable)
      if (!tableNode) return new Set<string>()
      const connected = bfsConnected(new Set([tableNode.id]), allEdges, 1)
      return new Set([...connected].filter((id) => {
        const n = allNodes.find((n) => n.id === id)
        return n && visibleTypes.has(n.type)
      }))
    }

    // Datasource or all
    return new Set(allNodes.filter((n) => {
      if (!visibleTypes.has(n.type)) return false
      if (selectedDatasource === '__all__') return true
      const ds = nodeToDatasource.get(n.id)
      return ds ? ds === selectedDatasource : true
    }).map((n) => n.id))
  }, [allNodes, allEdges, selectedDatasource, selectedTable, selectedMetric, visibleTypes, nodeToDatasource])

  const visibleEdges = useMemo(() =>
    allEdges.filter((e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target)),
    [allEdges, visibleNodeIds],
  )

  // Re-layout and auto-fit when filter changes
  useEffect(() => {
    if (visibleNodeIds.size === 0) return
    const canvas = canvasRef.current
    const cw = canvas?.clientWidth || 800
    const ch = canvas?.clientHeight || 500

    // Position visible nodes in a circular layout centered on canvas
    const visibleArr = nodesRef.current.filter((n) => visibleNodeIds.has(n.id))
    const baseRadius = Math.min(cw, ch) * 0.3

    // Layout: put "important" nodes (Table, Metric, DataSource) in inner ring, others outer
    const inner = visibleArr.filter((n) => ['Table', 'Metric', 'DataSource'].includes(n.type))
    const outer = visibleArr.filter((n) => !['Table', 'Metric', 'DataSource'].includes(n.type))

    inner.forEach((n, i) => {
      const angle = (i / Math.max(inner.length, 1)) * 2 * Math.PI - Math.PI / 2
      n.x = cw / 2 + Math.cos(angle) * baseRadius * 0.5
      n.y = ch / 2 + Math.sin(angle) * baseRadius * 0.5
      n.vx = 0; n.vy = 0
    })
    outer.forEach((n, i) => {
      const angle = (i / Math.max(outer.length, 1)) * 2 * Math.PI - Math.PI / 4
      n.x = cw / 2 + Math.cos(angle) * baseRadius
      n.y = ch / 2 + Math.sin(angle) * baseRadius
      n.vx = 0; n.vy = 0
    })

    // Reset simulation energy
    alphaRef.current = 0.8
    filterKeyRef.current += 1

    // Auto-fit after a short simulation settle
    const fitTimer = setTimeout(() => fitToNodes(visibleArr), 600)
    return () => clearTimeout(fitTimer)
  }, [visibleNodeIds, fitToNodes])

  // Force simulation — runs continuously, operates on nodesRef
  useEffect(() => {
    if (allNodes.length === 0) return

    const nodeById = new Map(nodesRef.current.map((n) => [n.id, n]))

    const tick = () => {
      const alpha = alphaRef.current
      if (alpha > 0.001) alphaRef.current *= 0.992

      const visible = nodesRef.current.filter((n) => visibleNodeIds.has(n.id))
      if (visible.length === 0) { animRef.current = requestAnimationFrame(tick); draw(visible); return }

      // Centering: pull toward center of visible bounds
      let cx = 0, cy = 0
      for (const n of visible) { cx += n.x; cy += n.y }
      cx /= visible.length; cy /= visible.length
      const canvas = canvasRef.current
      const targetCx = (canvas?.clientWidth || 800) / 2 / zoomRef.current
      const targetCy = (canvas?.clientHeight || 500) / 2 / zoomRef.current
      for (const n of visible) {
        n.vx += (targetCx - cx) * 0.0002 * alpha
        n.vy += (targetCy - cy) * 0.0002 * alpha
      }

      // Repulsion
      for (let i = 0; i < visible.length; i++) {
        for (let j = i + 1; j < visible.length; j++) {
          const a = visible[i], b = visible[j]
          const dx = b.x - a.x, dy = b.y - a.y
          const distSq = dx * dx + dy * dy || 1
          const dist = Math.sqrt(distSq)
          if (dist < 400) {
            const force = 800 * alpha / distSq
            a.vx -= dx * force; a.vy -= dy * force
            b.vx += dx * force; b.vy += dy * force
          }
        }
      }

      // Edge attraction
      for (const e of visibleEdges) {
        const a = nodeById.get(e.source), b = nodeById.get(e.target)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const idealDist = 120
        const force = (dist - idealDist) * 0.005 * alpha / dist
        a.vx += dx * force; a.vy += dy * force
        b.vx -= dx * force; b.vy -= dy * force
      }

      // Drag neighbor pull
      if (dragRef.current) {
        const dragged = nodeById.get(dragRef.current)
        if (dragged) {
          for (const e of visibleEdges) {
            let nbId: string | null = null
            if (e.source === dragRef.current) nbId = e.target
            else if (e.target === dragRef.current) nbId = e.source
            if (!nbId) continue
            const nb = nodeById.get(nbId)
            if (!nb || !visibleNodeIds.has(nb.id)) continue
            const dx = dragged.x - nb.x, dy = dragged.y - nb.y
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            if (dist > 160) {
              const pull = (dist - 160) * 0.01 / dist
              nb.vx += dx * pull; nb.vy += dy * pull
            }
          }
        }
      }

      // Apply velocity
      for (const n of visible) {
        if (n.id === dragRef.current) continue
        n.vx *= 0.88; n.vy *= 0.88
        n.x += n.vx; n.y += n.vy
      }

      draw(visible)
      animRef.current = requestAnimationFrame(tick)
    }

    animRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(animRef.current)
  }, [allNodes, visibleNodeIds, visibleEdges])

  const draw = (visible?: SimNode[]) => {
    const canvas = canvasRef.current; if (!canvas) return
    const ctx = canvas.getContext('2d'); if (!ctx) return
    const container = containerRef.current
    if (container) { canvas.width = container.clientWidth; canvas.height = container.clientHeight }

    const nodes = visible || nodesRef.current.filter((n) => visibleNodeIds.has(n.id))
    const pX = panXRef.current, pY = panYRef.current, z = zoomRef.current

    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.save()
    ctx.translate(pX, pY)
    ctx.scale(z, z)

    const style = getComputedStyle(document.documentElement)
    const labelColor = style.getPropertyValue('--text').trim() || '#1a1d2e'
    const dimColor = style.getPropertyValue('--text-dim').trim() || '#6b7085'
    const edgeColor = style.getPropertyValue('--graph-edge').trim() || 'rgba(45, 49, 72, 0.6)'

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Edges
    ctx.lineWidth = 1.2 / z
    for (const e of visibleEdges) {
      const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
      if (!a || !b) continue
      ctx.strokeStyle = edgeColor
      ctx.beginPath()
      ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()

      // Edge label
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
      ctx.fillStyle = dimColor
      ctx.font = `${9 / Math.max(z, 0.6)}px Inter, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.fillText(e.type, mx, my - 4)
    }

    // Nodes
    for (const n of nodes) {
      const color = NODE_COLORS[n.type] || '#6c8cff'
      const radius = NODE_RADIUS[n.type] || 8
      const isHovered = n.id === hoveredId
      const isDragged = n.id === dragRef.current

      // Shadow for hovered/dragged
      if (isHovered || isDragged) {
        ctx.shadowColor = color
        ctx.shadowBlur = 12
      }

      ctx.beginPath()
      ctx.arc(n.x, n.y, radius, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()

      if (isHovered || isDragged) {
        ctx.shadowColor = 'transparent'
        ctx.shadowBlur = 0
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = 2.5 / z
        ctx.stroke()
      }

      // Embedding badge for Metric nodes
      if (n.type === 'Metric') {
        const hasEmb = n.properties?.hasEmbedding === true
        const badgeR = 4
        const bx = n.x + radius * 0.7
        const by = n.y - radius * 0.7
        ctx.beginPath()
        ctx.arc(bx, by, badgeR, 0, Math.PI * 2)
        ctx.fillStyle = hasEmb ? '#4ade80' : '#6b7085'
        ctx.fill()
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = 1.5 / z
        ctx.stroke()
      }

      // Label
      ctx.fillStyle = labelColor
      const fontSize = n.type === 'Column' || n.type === 'MetadataKey' ? 9 : 11
      ctx.font = `${fontSize}px Inter, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.fillText(n.label, n.x, n.y + radius + 13)
    }

    // Tooltip
    const hNode = hoveredId ? nodeMap.get(hoveredId) : null
    if (hNode) {
      const tx = hNode.x + 20, ty = hNode.y - 10
      const lines = [`${hNode.type}: ${hNode.label}`]
      if (hNode.datasource) lines.push(`Source: ${hNode.datasource}`)
      if (hNode.type === 'Metric') {
        lines.push(hNode.properties?.hasEmbedding ? 'Embedding: yes' : 'Embedding: no')
      }
      ctx.font = '12px Inter, system-ui, sans-serif'
      const maxW = Math.max(...lines.map((l) => ctx.measureText(l).width))
      const w = maxW + 24, h = lines.length * 20 + 12
      const bg = style.getPropertyValue('--bg-card').trim() || 'rgba(26, 29, 39, 0.95)'
      ctx.fillStyle = bg
      ctx.beginPath(); ctx.roundRect(tx, ty - 14, w, h, 6); ctx.fill()
      ctx.strokeStyle = edgeColor; ctx.lineWidth = 1 / z; ctx.stroke()
      ctx.fillStyle = labelColor; ctx.textAlign = 'left'
      for (let i = 0; i < lines.length; i++) ctx.fillText(lines[i], tx + 12, ty + 4 + i * 20)
    }

    ctx.restore()
  }

  // Redraw on pan/zoom
  useEffect(() => { draw() }, [panX, panY, zoom, hoveredId])

  const getNodeAt = useCallback((sx: number, sy: number): SimNode | null => {
    const { x: mx, y: my } = screenToWorld(sx, sy)
    const visible = nodesRef.current.filter((n) => visibleNodeIds.has(n.id))
    for (let i = visible.length - 1; i >= 0; i--) {
      const n = visible[i]
      const r = NODE_RADIUS[n.type] || 8
      const dx = mx - n.x, dy = my - n.y
      if (dx * dx + dy * dy < (r + 6) * (r + 6)) return n
    }
    return null
  }, [screenToWorld, visibleNodeIds])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect(); if (!rect) return
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top
    const node = getNodeAt(sx, sy)
    if (node) {
      dragRef.current = node.id
      // Boost alpha so dragged neighbors respond
      alphaRef.current = Math.max(alphaRef.current, 0.3)
    } else {
      isPanning.current = true
      panStart.current = { x: e.clientX, y: e.clientY, panX: panXRef.current, panY: panYRef.current }
    }
  }, [getNodeAt])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect(); if (!rect) return
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top

    if (isPanning.current) {
      setPanX(panStart.current.panX + (e.clientX - panStart.current.x))
      setPanY(panStart.current.panY + (e.clientY - panStart.current.y))
      return
    }

    if (dragRef.current) {
      const { x, y } = screenToWorld(sx, sy)
      const node = nodesRef.current.find((n) => n.id === dragRef.current)
      if (node) { node.x = x; node.y = y; node.vx = 0; node.vy = 0 }
    } else {
      const h = getNodeAt(sx, sy)
      setHoveredId(h ? h.id : null)
    }
  }, [screenToWorld, getNodeAt])

  const handleMouseUp = useCallback(() => {
    dragRef.current = null
    isPanning.current = false
  }, [])

  const toggleType = (type: string) => {
    setVisibleTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type); else next.add(type)
      return next
    })
  }

  const handleMetricChange = (label: string) => {
    setSelectedMetric(label)
    if (label === '__all__') {
      searchParams.delete('metric')
    } else {
      searchParams.set('metric', label)
    }
    setSearchParams(searchParams, { replace: true })
  }

  const filterDesc = useMemo(() => {
    const parts: string[] = []
    if (selectedMetric !== '__all__') parts.push(`metric: ${selectedMetric}`)
    else if (selectedDatasource !== '__all__') parts.push(selectedDatasource)
    if (selectedTable !== '__all__') parts.push(selectedTable)
    return parts.length ? ` (${parts.join(' / ')})` : ''
  }, [selectedDatasource, selectedTable, selectedMetric])

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error loading graph: {error}</div>

  return (
    <div className={`graph-page ${isFullscreen ? 'graph-fullscreen' : ''}`}>
      <div className="graph-toolbar">
        <div className="graph-toolbar-left">
          <div className="graph-filter-group">
            <label className="graph-filter-label">Metric</label>
            <select
              className="graph-filter-select"
              value={selectedMetric}
              onChange={(e) => handleMetricChange(e.target.value)}
            >
              <option value="__all__">All Metrics</option>
              {metricNodes.map((m) => (
                <option key={m.id} value={m.label}>{m.label}</option>
              ))}
            </select>
          </div>

          <div className="graph-filter-group">
            <label className="graph-filter-label">DataSource</label>
            <select
              className="graph-filter-select"
              value={selectedDatasource}
              onChange={(e) => { setSelectedDatasource(e.target.value); setSelectedMetric('__all__'); searchParams.delete('metric'); setSearchParams(searchParams, { replace: true }) }}
              disabled={selectedMetric !== '__all__'}
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
              onChange={(e) => { setSelectedTable(e.target.value); setSelectedMetric('__all__'); searchParams.delete('metric'); setSearchParams(searchParams, { replace: true }) }}
              disabled={selectedMetric !== '__all__'}
            >
              <option value="__all__">All Tables</option>
              {tables.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div className="graph-filter-group">
            <label className="graph-filter-label">Types</label>
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
            {visibleNodeIds.size} nodes, {visibleEdges.length} edges{filterDesc}
          </span>
        </div>
      </div>

      <div className="graph-container graph-container-fill" ref={containerRef}>
        <canvas
          ref={canvasRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          style={{ cursor: isPanning.current ? 'grabbing' : dragRef.current ? 'grabbing' : hoveredId ? 'grab' : 'default' }}
        />

        <div className="graph-zoom-controls">
          <button onClick={handleZoomIn} title="Zoom in">+</button>
          <button onClick={handleZoomOut} title="Zoom out">&minus;</button>
          <button onClick={handleFitToScreen} title="Fit to screen">&#x2922;</button>
          <button onClick={handleResetZoom} title="Reset zoom">1:1</button>
          <button onClick={toggleFullscreen} title={isFullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}>
            {isFullscreen ? '\u2716' : '\u26F6'}
          </button>
        </div>

        <div className="graph-zoom-indicator">{Math.round(zoom * 100)}%</div>
      </div>
    </div>
  )
}
