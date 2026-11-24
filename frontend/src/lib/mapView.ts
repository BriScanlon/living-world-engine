/**
 * Where the map image sits on the table, in table CSS px.
 *
 * True-scale mode makes one 5 ft square of the painted map exactly one calibrated
 * inch: `pxPerSquare` image pixels -> `hexPx` CSS px. Because it is defined against the
 * calibrated inch (not the screen), it stays correct if calibration or OS scaling changes.
 */
import { useCallback, useState } from 'react'
import type { Asset, MapView } from './types'

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

export interface Size {
  w: number
  h: number
}

/** Map is painted 140 ft across -> 28 five-foot squares (AI-generated + starter maps). */
export const AI_SQUARES_ACROSS = 28
export const FEET_PER_SQUARE = 5
export const DEFAULT_VIEW: MapView = { mode: 'cover', pxPerSquare: 100, cx: 0.5, cy: 0.5 }

export function mapRect(view: MapView, natural: Size | null, viewport: Size, hexPx: number): Rect {
  if (!natural || natural.w <= 0 || natural.h <= 0) {
    return { left: 0, top: 0, width: viewport.w, height: viewport.h }
  }
  let s: number
  if (view.mode === 'scale') s = hexPx / Math.max(0.01, view.pxPerSquare)
  else if (view.mode === 'contain') s = Math.min(viewport.w / natural.w, viewport.h / natural.h)
  else s = Math.max(viewport.w / natural.w, viewport.h / natural.h)
  const width = natural.w * s
  const height = natural.h * s
  return {
    left: viewport.w / 2 - view.cx * width,
    top: viewport.h / 2 - view.cy * height,
    width,
    height,
  }
}

export function scaleRect(r: Rect, k: number): Rect {
  return { left: r.left * k, top: r.top * k, width: r.width * k, height: r.height * k }
}

/** Size of the map in 5 ft squares at a given pxPerSquare. */
export function squaresAcross(natural: Size, pxPerSquare: number) {
  return { x: natural.w / pxPerSquare, y: natural.h / pxPerSquare }
}

/**
 * Natural (image-pixel) size of a map. Uses the DB dimensions when known (for AI videos
 * these are the 4K still's, which the video matches in aspect), else measures on load.
 */
export function useNaturalSize(map: Asset | null): [Size | null, (w: number, h: number) => void] {
  const [measured, setMeasured] = useState<{ id: string; w: number; h: number } | null>(null)
  const id = map?.id
  const onNatural = useCallback(
    (w: number, h: number) => {
      if (id && w > 0 && h > 0) setMeasured((m) => (m && m.id === id && m.w === w && m.h === h ? m : { id, w, h }))
    },
    [id],
  )
  if (!map) return [null, onNatural]
  if (map.width && map.height) return [{ w: map.width, h: map.height }, onNatural]
  return [measured && measured.id === map.id ? { w: measured.w, h: measured.h } : null, onNatural]
}
