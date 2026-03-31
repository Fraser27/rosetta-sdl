import { useEffect, useRef, useState } from 'react'
import { api, type GraphNode, type GraphEdge } from '../api'

const NODE_COLORS: Record<string, string> = {
  Table: '#6c8cff',
  Column: '#8b90a5',
  Metric: '#4ade80',
  BusinessTerm: '#fbbf24',
  DataSource: '#a78bfa',
  Document: '#f87171',
  Concept: '#fb923c',
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
  const [nodes, setNodes] = useState<SimNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hovered, setHovered] = useState<SimNode | null>(null)
  const dragRef = useRef<SimNode | null>(null)
  const animRef = useRef<number>(0)

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
        setNodes(simNodes)
        setEdges(e)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Force simulation
  useEffect(() => {
    if (nodes.length === 0) return

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Build adjacency list for drag-pulling neighbors
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

      // Weak center gravity — keeps graph on screen but lets subgraphs spread
      for (const n of nodes) {
        n.vx += (cw / 2 - n.x) * 0.0004 * alpha
        n.vy += (ch / 2 - n.y) * 0.0004 * alpha
      }

      // Strong long-range repulsion — pushes all nodes apart
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

      // Attraction along edges — pulls connected nodes to ideal distance
      for (const e of edges) {
        const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const force = (dist - 140) * 0.004 * alpha / dist
        a.vx += dx * force; a.vy += dy * force
        b.vx -= dx * force; b.vy -= dy * force
      }

      // When dragging, pull neighbors toward the dragged node
      if (dragRef.current) {
        const dragged = dragRef.current
        const nbs = neighbors.get(dragged.id)
        if (nbs) {
          for (const nbId of nbs) {
            const nb = nodeMap.get(nbId)
            if (!nb) continue
            const dx = dragged.x - nb.x, dy = dragged.y - nb.y
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            // Pull neighbors gently — only if far, and cap at ideal distance
            if (dist > 180) {
              const pull = (dist - 180) * 0.008 / dist
              nb.vx += dx * pull
              nb.vy += dy * pull
            }
          }
        }
      }

      // Apply velocity with damping + keep in bounds
      for (const n of nodes) {
        if (n === dragRef.current) continue
        n.vx *= 0.9; n.vy *= 0.9
        n.x += n.vx; n.y += n.vy
        // Soft boundary — bounce off edges
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

    // Read CSS vars for theme-aware colors
    const style = getComputedStyle(document.documentElement)
    const labelColor = style.getPropertyValue('--text').trim() || '#1a1d2e'
    const dimColor = style.getPropertyValue('--text-dim').trim() || '#6b7085'
    const edgeColor = style.getPropertyValue('--graph-edge').trim() || 'rgba(45, 49, 72, 0.6)'

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Draw edges
    ctx.strokeStyle = edgeColor
    ctx.lineWidth = 1
    for (const e of edges) {
      const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
      if (!a || !b) continue
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()

      // Edge label
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
      ctx.fillStyle = dimColor
      ctx.font = '9px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(e.type, mx, my - 4)
    }

    // Draw nodes
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

      // Label
      ctx.fillStyle = labelColor
      ctx.font = n.type === 'Column' ? '9px Inter, sans-serif' : '11px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(n.label, n.x, n.y + radius + 14)
    }

    // Tooltip for hovered node
    if (hovered) {
      const x = hovered.x + 16, y = hovered.y - 10
      const text = `${hovered.type}: ${hovered.label}`
      ctx.font = '12px Inter, sans-serif'
      const w = ctx.measureText(text).width + 16
      const tooltipBg = style.getPropertyValue('--bg-card').trim() || 'rgba(26, 29, 39, 0.95)'
      ctx.fillStyle = tooltipBg
      ctx.beginPath()
      ctx.roundRect(x, y - 14, w, 24, 4)
      ctx.fill()
      ctx.strokeStyle = edgeColor
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = labelColor
      ctx.textAlign = 'left'
      ctx.fillText(text, x + 8, y + 3)
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

  if (loading) return <div className="loading"><div className="spinner" /></div>
  if (error) return <div className="empty-state">Error loading graph: {error}</div>

  return (
    <>
      <div className="page-header">
        <h2>Graph Explorer</h2>
        <p>Interactive visualization of the semantic layer ontology</p>
      </div>

      <div className="graph-legend">
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <div className="graph-legend-item" key={type}>
            <div className="graph-legend-dot" style={{ background: color }} />
            {type}
          </div>
        ))}
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
          {nodes.length} nodes, {edges.length} edges. Drag nodes to rearrange.
        </p>
      </div>
    </>
  )
}
