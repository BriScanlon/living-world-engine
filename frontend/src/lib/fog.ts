/**
 * Fog of war.
 *
 * The mask is an ordered list of reveal/hide operations in coordinates normalised to
 * the table viewport (x,y in 0..1, circle radius relative to viewport width), so the
 * same ops render identically on the 4K table and on a small DM preview.
 *
 * FogStore keeps the op list outside React so brush strokes stay O(1) per op.
 */
import type { FogOp, FogState } from './types'

type Listener = (evt: { kind: 'full' } | { kind: 'op'; op: FogOp }) => void

export class FogStore {
  enabled = false
  base: 'hidden' | 'revealed' = 'hidden'
  ops: FogOp[] = []
  private listeners = new Set<Listener>()

  setFull(fog: FogState) {
    this.enabled = fog.enabled
    this.base = fog.base
    this.ops = fog.ops.slice()
    this.emit({ kind: 'full' })
  }

  addOp(op: FogOp) {
    this.ops.push(op)
    this.emit({ kind: 'op', op })
  }

  subscribe(fn: Listener) {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  private emit(evt: Parameters<Listener>[0]) {
    this.listeners.forEach((l) => l(evt))
  }
}

/** Soft edge: fraction of the radius that is fully cleared/filled. */
const SOFT_CORE = 0.72

export function applyFogOp(ctx: CanvasRenderingContext2D, w: number, h: number, op: FogOp, color: string) {
  ctx.save()
  ctx.globalCompositeOperation = op.mode === 'reveal' ? 'destination-out' : 'source-over'
  if (op.shape === 'circle') {
    const x = op.x * w
    const y = op.y * h
    const r = Math.max(0.5, op.r * w)
    const g = ctx.createRadialGradient(x, y, 0, x, y, r)
    const solid = op.mode === 'reveal' ? 'rgba(0,0,0,1)' : color
    const clear = op.mode === 'reveal' ? 'rgba(0,0,0,0)' : withAlpha(color, 0)
    g.addColorStop(0, solid)
    g.addColorStop(SOFT_CORE, solid)
    g.addColorStop(1, clear)
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fill()
  } else {
    ctx.fillStyle = op.mode === 'reveal' ? '#000' : color
    ctx.beginPath()
    op.pts.forEach(([px, py], i) => (i === 0 ? ctx.moveTo(px * w, py * h) : ctx.lineTo(px * w, py * h)))
    ctx.closePath()
    ctx.fill()
  }
  ctx.restore()
}

export function renderFog(ctx: CanvasRenderingContext2D, w: number, h: number, store: FogStore, color: string) {
  ctx.save()
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height)
  ctx.restore()
  if (store.base === 'hidden') {
    ctx.fillStyle = color
    ctx.fillRect(0, 0, w, h)
  }
  for (const op of store.ops) applyFogOp(ctx, w, h, op, color)
}

function withAlpha(color: string, a: number) {
  // Accepts #rgb / #rrggbb; anything else falls back to black.
  let hex = color.replace('#', '')
  if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('')
  const n = parseInt(hex, 16)
  if (Number.isNaN(n) || hex.length !== 6) return `rgba(0,0,0,${a})`
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}
