import { useEffect, useRef } from 'react'
import { applyFogOp, renderFog, type FogStore } from '../lib/fog'
import type { Rect } from '../lib/mapView'

interface Props {
  store: FogStore
  enabled: boolean
  /** The map's rectangle in container CSS px — fog is anchored to the map, not the screen. */
  rect: Rect
  /** Canvas pixels per CSS pixel. Fog edges are soft, so < devicePixelRatio is fine. */
  resolution: number
  color: string
  opacity: number
}

/** Keep huge true-scale maps from allocating enormous canvases. */
const MAX_PIXELS = 16_000_000
const MAX_SIDE = 8192

/** Layer 4: fog of war, rendered incrementally from the shared FogStore. */
export function FogCanvas({ store, enabled, rect, resolution, color, opacity }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  // Only re-rasterise when the size changes; panning is just a CSS move.
  const cssW = Math.round(rect.width)
  const cssH = Math.round(rect.height)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || cssW < 1 || cssH < 1) return
    const res = Math.min(resolution, Math.sqrt(MAX_PIXELS / (cssW * cssH)), MAX_SIDE / Math.max(cssW, cssH))
    const w = Math.max(1, Math.round(cssW * res))
    const h = Math.max(1, Math.round(cssH * res))
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')!
    renderFog(ctx, w, h, store, color)
    return store.subscribe((evt) => {
      if (evt.kind === 'full') renderFog(ctx, w, h, store, color)
      else applyFogOp(ctx, w, h, evt.op, color)
    })
  }, [store, cssW, cssH, resolution, color])

  return (
    <canvas
      ref={ref}
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        pointerEvents: 'none',
        opacity: enabled ? opacity : 0,
        transition: 'opacity 400ms ease',
      }}
    />
  )
}
