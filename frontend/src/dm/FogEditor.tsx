import { useCallback, useEffect, useRef, useState } from 'react'
import { FogCanvas } from '../components/FogCanvas'
import { GridCanvas } from '../components/GridCanvas'
import { MapMedia } from '../components/MapMedia'
import type { FogStore } from '../lib/fog'
import { FEET_PER_SQUARE, mapRect, scaleRect, type Size } from '../lib/mapView'
import type { useTableSync } from '../lib/sync'
import { useDevicePixelRatio, useElementSize } from '../lib/useElementSize'
import type { FogMode, FogOp, TableState } from '../lib/types'
import { Button, Panel, Segmented, Slider, Toggle } from './ui'

interface Props {
  state: TableState
  fog: FogStore
  fogMeta: { enabled: boolean; base: 'hidden' | 'revealed'; count: number }
  sendFogOp: (op: FogOp) => void
  send: (msg: unknown) => void
  patch: ReturnType<typeof useTableSync>['patch']
  natural: Size | null
  onNatural: (w: number, h: number) => void
}

type Tool = 'brush' | 'poly' | 'measure' | 'pan'
type Pt = [number, number]

/**
 * Live mirror of the table. Fog brush/polygon, a Measure tool to set the map's
 * 1" = 5 ft scale from a known distance, and a Pan tool to slide the map under the grid.
 * Fog and measurements use map-normalised coordinates (0..1 of the image).
 */
export function FogEditor({ state, fog, fogMeta, sendFogOp, send, patch, natural, onNatural }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const { w: width } = useElementSize(wrapRef)
  const dpr = useDevicePixelRatio()
  const tv = state.tableViewport
  const height = width > 0 ? (width * tv.h) / tv.w : 0
  const hexPx = state.calibration.hexFlatToFlatPx
  const view = state.view

  // Map rectangle in table CSS px, and the same rectangle in preview px.
  const rectT = mapRect(view, natural, tv, hexPx)
  const vs = width > 0 ? width / tv.w : 1
  const rectP = scaleRect(rectT, vs)

  const [tool, setTool] = useState<Tool>('brush')
  const [mode, setMode] = useState<FogMode>('reveal')
  const [brushIn, setBrushIn] = useState(3)
  const [showGrid, setShowGrid] = useState(true)
  const [cursor, setCursor] = useState<Pt | null>(null) // map coords
  const [poly, setPoly] = useState<Pt[]>([])
  const [line, setLine] = useState<{ a: Pt; b: Pt } | null>(null)
  const [measured, setMeasured] = useState<{ imagePx: number; feet: string } | null>(null)

  const strokes = useRef<number[]>([])
  const current = useRef<{ count: number; last: Pt } | null>(null)
  const expectingFull = useRef(false)
  const drag = useRef<{ kind: 'measure' | 'pan'; startPx: Pt; cx: number; cy: number } | null>(null)
  const panFrame = useRef<number>(0)
  const pendingPan = useRef<{ cx: number; cy: number } | null>(null)

  // Brush radius, normalised to the map width (fog op units).
  const rNorm = ((brushIn / 2) * hexPx) / Math.max(1, rectT.width)

  useEffect(
    () =>
      fog.subscribe((e) => {
        if (e.kind !== 'full') return
        if (expectingFull.current) expectingFull.current = false
        else strokes.current = []
      }),
    [fog],
  )

  const ensureEnabled = useCallback(() => {
    if (!fog.enabled) send({ type: 'fog_enabled', enabled: true })
  }, [fog, send])

  const previewPt = (e: React.PointerEvent | React.MouseEvent): Pt => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top]
  }
  const toMap = ([px, py]: Pt): Pt => [(px - rectP.left) / rectP.width, (py - rectP.top) / rectP.height]
  const toPreview = ([mx, my]: Pt): Pt => [rectP.left + mx * rectP.width, rectP.top + my * rectP.height]
  const imagePxBetween = (a: Pt, b: Pt) =>
    natural ? Math.hypot((b[0] - a[0]) * natural.w, (b[1] - a[1]) * natural.h) : 0

  const stamp = (x: number, y: number) => {
    sendFogOp({ mode, shape: 'circle', x, y, r: rNorm })
    if (current.current) {
      current.current.count++
      current.current.last = [x, y]
    }
  }

  const finishPoly = useCallback(() => {
    setPoly((p) => {
      if (p.length >= 3) {
        ensureEnabled()
        sendFogOp({ mode, shape: 'poly', pts: p })
        strokes.current.push(1)
      }
      return []
    })
  }, [mode, sendFogOp, ensureEnabled])

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 2) {
      setPoly([])
      setLine(null)
      return
    }
    const pp = previewPt(e)
    const [x, y] = toMap(pp)
    const capture = () => (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    if (tool === 'poly') {
      if (poly.length >= 3) {
        const [fx, fy] = toPreview(poly[0])
        if (Math.hypot(fx - pp[0], fy - pp[1]) < 12) return finishPoly()
      }
      setPoly((p) => [...p, [x, y]])
    } else if (tool === 'measure') {
      if (!natural) return
      capture()
      drag.current = { kind: 'measure', startPx: pp, cx: view.cx, cy: view.cy }
      setMeasured(null)
      setLine({ a: [x, y], b: [x, y] })
    } else if (tool === 'pan') {
      capture()
      drag.current = { kind: 'pan', startPx: pp, cx: view.cx, cy: view.cy }
    } else {
      ensureEnabled()
      capture()
      current.current = { count: 0, last: [x, y] }
      stamp(x, y)
    }
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const pp = previewPt(e)
    const [x, y] = toMap(pp)
    setCursor([x, y])
    const d = drag.current
    if (d?.kind === 'measure') {
      setLine((l) => (l ? { a: l.a, b: [x, y] } : l))
      return
    }
    if (d?.kind === 'pan') {
      // preview px -> table px -> fraction of the map
      const dx = (pp[0] - d.startPx[0]) / vs / Math.max(1, rectT.width)
      const dy = (pp[1] - d.startPx[1]) / vs / Math.max(1, rectT.height)
      pendingPan.current = { cx: d.cx - dx, cy: d.cy - dy }
      if (!panFrame.current)
        panFrame.current = requestAnimationFrame(() => {
          panFrame.current = 0
          if (pendingPan.current) patch('view', pendingPan.current)
        })
      return
    }
    const cur = current.current
    if (!cur || tool !== 'brush') return
    // interpolate so fast strokes stay continuous; spacing measured in table px
    const [lx, ly] = cur.last
    const dist = Math.hypot((x - lx) * rectT.width, (y - ly) * rectT.height)
    const spacing = Math.max(2, rNorm * rectT.width * 0.35)
    if (dist < spacing) return
    const steps = Math.floor(dist / spacing)
    for (let i = 1; i <= steps; i++) stamp(lx + ((x - lx) * i) / steps, ly + ((y - ly) * i) / steps)
  }

  const onPointerUp = () => {
    const d = drag.current
    drag.current = null
    if (d?.kind === 'measure' && line) {
      const imagePx = imagePxBetween(line.a, line.b)
      if (imagePx < 4) {
        setLine(null)
        return
      }
      // guess the distance at the current scale, rounded to whole squares
      const squares = Math.max(1, Math.round(imagePx / view.pxPerSquare))
      setMeasured({ imagePx, feet: String(squares * FEET_PER_SQUARE) })
      return
    }
    if (current.current && current.current.count > 0) strokes.current.push(current.current.count)
    current.current = null
  }

  const applyMeasure = () => {
    if (!measured) return
    const feet = parseFloat(measured.feet)
    if (!Number.isFinite(feet) || feet <= 0) return
    const pxPerSquare = Math.round((measured.imagePx / (feet / FEET_PER_SQUARE)) * 1000) / 1000
    const next = { mode: 'scale' as const, pxPerSquare, cx: view.cx, cy: view.cy }
    // Square grid: the line was started on a painted grid corner, so slide the map until
    // that corner sits exactly on the nearest table grid intersection.
    if (state.grid.type === 'square' && natural && line) {
      const r = mapRect(next, natural, tv, hexPx)
      const snap = (pos: number, offset: number) => offset + Math.round((pos - offset) / hexPx) * hexPx
      const ax = r.left + line.a[0] * r.width
      const ay = r.top + line.a[1] * r.height
      next.cx += (ax - snap(ax, state.grid.offsetX)) / r.width
      next.cy += (ay - snap(ay, state.grid.offsetY)) / r.height
    }
    patch('view', next)
    setMeasured(null)
    setLine(null)
  }

  const undo = () => {
    const n = strokes.current.pop()
    if (!n) return
    expectingFull.current = true
    send({ type: 'fog_undo', count: n })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Escape') {
        setPoly([])
        setLine(null)
        setMeasured(null)
      }
      if (e.key === 'Enter') finishPoly()
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // Overlay: brush cursor, polygon in progress, measuring line.
  useEffect(() => {
    const c = overlayRef.current
    if (!c || width < 1) return
    c.width = Math.round(width * dpr)
    c.height = Math.round(height * dpr)
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const color = mode === 'reveal' ? '#fbbf24' : '#60a5fa'
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    if (tool === 'brush' && cursor) {
      const [px, py] = toPreview(cursor)
      ctx.beginPath()
      ctx.arc(px, py, rNorm * rectP.width, 0, Math.PI * 2)
      ctx.stroke()
    }
    if (tool === 'poly' && poly.length) {
      ctx.fillStyle = mode === 'reveal' ? 'rgba(251,191,36,0.15)' : 'rgba(96,165,250,0.2)'
      ctx.beginPath()
      poly.forEach((p, i) => {
        const [px, py] = toPreview(p)
        if (i) ctx.lineTo(px, py)
        else ctx.moveTo(px, py)
      })
      if (cursor) ctx.lineTo(...toPreview(cursor))
      ctx.closePath()
      ctx.fill()
      ctx.stroke()
      poly.forEach((p, i) => {
        const [px, py] = toPreview(p)
        ctx.beginPath()
        ctx.arc(px, py, i === 0 ? 6 : 3.5, 0, Math.PI * 2)
        ctx.fillStyle = i === 0 ? color : '#fff'
        ctx.fill()
      })
    }
    if (line) {
      const [ax, ay] = toPreview(line.a)
      const [bx, by] = toPreview(line.b)
      ctx.strokeStyle = '#22d3ee'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(ax, ay)
      ctx.lineTo(bx, by)
      ctx.stroke()
      const ang = Math.atan2(by - ay, bx - ax) + Math.PI / 2
      for (const [x, y] of [
        [ax, ay],
        [bx, by],
      ]) {
        ctx.beginPath()
        ctx.moveTo(x - Math.cos(ang) * 7, y - Math.sin(ang) * 7)
        ctx.lineTo(x + Math.cos(ang) * 7, y + Math.sin(ang) * 7)
        ctx.stroke()
      }
      const px = imagePxBetween(line.a, line.b)
      const ft = (px / view.pxPerSquare) * FEET_PER_SQUARE
      const label = `${Math.round(px)} image px · ${ft.toFixed(1)} ft at current scale`
      ctx.font = '600 12px system-ui, sans-serif'
      const tw = ctx.measureText(label).width
      const lx = Math.min(Math.max((ax + bx) / 2 - tw / 2, 4), width - tw - 8)
      const ly = Math.max((ay + by) / 2 - 14, 16)
      ctx.fillStyle = 'rgba(0,0,0,0.75)'
      ctx.fillRect(lx - 4, ly - 13, tw + 8, 18)
      ctx.fillStyle = '#a5f3fc'
      ctx.fillText(label, lx, ly)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, poly, line, tool, mode, rNorm, width, height, dpr, rectP.left, rectP.top, rectP.width, rectP.height, view.pxPerSquare])

  const cursorStyle = tool === 'pan' ? (drag.current ? 'grabbing' : 'grab') : tool === 'brush' ? 'none' : 'crosshair'
  const hints: Record<Tool, string> = {
    brush: 'Drag to paint fog · Ctrl+Z undoes the last stroke',
    poly: 'Click to add points · click the first point, double-click or Enter to close · Esc / right-click cancels',
    measure:
      'Drag across a known distance (e.g. 10 painted squares), then enter its length in feet · on a square grid, start on a painted corner and the map snaps into alignment',
    pan: 'Drag to slide the map under the grid · fog moves with the map',
  }

  return (
    <Panel
      title="Table Preview"
      actions={
        <span className="text-xs text-slate-400">
          {state.map ? state.map.title : 'No map on table'} · {tv.w}×{tv.h} @{tv.dpr}x
        </span>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="w-[26rem]">
          <Segmented
            value={tool}
            onChange={(t) => {
              setTool(t)
              setPoly([])
              if (t !== 'measure') {
                setLine(null)
                setMeasured(null)
              }
            }}
            options={[
              { value: 'brush', label: '◯ Brush' },
              { value: 'poly', label: '⬠ Polygon' },
              { value: 'measure', label: '📏 Measure' },
              { value: 'pan', label: '✥ Pan' },
            ]}
          />
        </div>
        {(tool === 'brush' || tool === 'poly') && (
          <div className="w-36">
            <Segmented
              value={mode}
              onChange={setMode}
              options={[
                { value: 'reveal', label: 'Reveal' },
                { value: 'hide', label: 'Hide' },
              ]}
            />
          </div>
        )}
        {tool === 'brush' && (
          <div className="w-44">
            <Slider label="Brush" value={brushIn} min={0.5} max={12} step={0.5} onChange={setBrushIn} format={(v) => `${v}" · ${v * 5} ft`} />
          </div>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button onClick={undo} title="Undo last fog stroke (Ctrl+Z)">
            ↶ Undo
          </Button>
          <Button onClick={() => send({ type: 'fog_reset', base: 'revealed' })}>Reveal All</Button>
          <Button onClick={() => send({ type: 'fog_reset', base: 'hidden' })}>Hide All</Button>
        </div>
      </div>

      {measured && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-cyan-950/60 px-3 py-2 text-sm ring-1 ring-cyan-500/40">
          <span className="text-cyan-200">
            Line is <span className="font-mono">{Math.round(measured.imagePx)}</span> image px. That distance is
          </span>
          <input
            autoFocus
            value={measured.feet}
            onChange={(e) => setMeasured({ ...measured, feet: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && applyMeasure()}
            inputMode="decimal"
            className="w-20 rounded bg-slate-900 px-2 py-1 font-mono ring-1 ring-cyan-500/40 outline-none focus:ring-cyan-300"
          />
          <span className="text-cyan-200">ft</span>
          <span className="font-mono text-xs text-cyan-300/70">
            → {(measured.imagePx / (parseFloat(measured.feet) / FEET_PER_SQUARE) || 0).toFixed(1)} px per 5 ft
          </span>
          <Button variant="primary" onClick={applyMeasure}>
            Set 1" = 5 ft scale
          </Button>
          <Button
            variant="subtle"
            onClick={() => {
              setMeasured(null)
              setLine(null)
            }}
          >
            Cancel
          </Button>
        </div>
      )}

      <div ref={wrapRef} className="relative w-full overflow-hidden rounded-lg bg-black ring-1 ring-white/10" style={{ height }}>
        {state.map && (
          <MapMedia map={state.map} rect={rectP} playbackRate={state.display.playbackRate} preferStill onNaturalSize={onNatural} />
        )}
        {showGrid && <GridCanvas grid={state.grid} hexPx={hexPx} table={tv} width={width} height={height} dpr={dpr} />}
        <FogCanvas store={fog} enabled={fogMeta.enabled} rect={rectP} resolution={dpr} color="#0b1020" opacity={0.62} />
        <canvas
          ref={overlayRef}
          className="absolute inset-0 h-full w-full touch-none"
          style={{ cursor: cursorStyle }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={() => setCursor(null)}
          onDoubleClick={() => tool === 'poly' && finishPoly()}
          onContextMenu={(e) => e.preventDefault()}
        />
        {!fogMeta.enabled && (tool === 'brush' || tool === 'poly') && (
          <div className="pointer-events-none absolute top-2 left-2 rounded bg-black/70 px-2 py-1 text-xs text-slate-300">
            Fog of war is off — painting turns it on
          </div>
        )}
        {tool === 'measure' && !state.map && (
          <div className="pointer-events-none absolute top-2 left-2 rounded bg-black/70 px-2 py-1 text-xs text-slate-300">
            Push a map to the table first
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1">
        <div className="w-48">
          <Toggle label="Fog of war on table" checked={fogMeta.enabled} onChange={(v) => send({ type: 'fog_enabled', enabled: v })} />
        </div>
        <div className="w-48">
          <Toggle label="Show grid in preview" checked={showGrid} onChange={setShowGrid} />
        </div>
        <p className="text-xs text-slate-500">
          {hints[tool]} · {fogMeta.count} fog ops
        </p>
      </div>
    </Panel>
  )
}
