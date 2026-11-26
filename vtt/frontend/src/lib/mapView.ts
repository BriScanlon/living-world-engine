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

/** Image px per 5 ft square the map is *currently displayed* at (works for fill/fit too). */
export function effectivePxPerSquare(view: MapView, natural: Size, viewport: Size, hexPx: number) {
  return (hexPx * natural.w) / mapRect(view, natural, viewport, hexPx).width
}

/**
 * Zoom by `factor` (>1 = map bigger) keeping the map point under `anchorFrom` (table px)
 * under `anchorTo` (defaults to the same spot), so wheel/pinch zoom feels "under the finger".
 * Always returns a true-scale view, starting from whatever size the map is shown at now.
 */
export function zoomAt(
  base: MapView,
  natural: Size,
  viewport: Size,
  hexPx: number,
  factor: number,
  anchorFrom: [number, number],
  anchorTo: [number, number] = anchorFrom,
): MapView {
  const r = mapRect(base, natural, viewport, hexPx)
  const px = (hexPx * natural.w) / r.width
  const pxNew = Math.min(20000, Math.max(1, px / factor))
  const k = px / pxNew
  const w = r.width * k
  const h = r.height * k
  const mx = (anchorFrom[0] - r.left) / r.width
  const my = (anchorFrom[1] - r.top) / r.height
  const left = anchorTo[0] - mx * w
  const top = anchorTo[1] - my * h
  return {
    mode: 'scale',
    pxPerSquare: Math.round(pxNew * 1000) / 1000,
    cx: (viewport.w / 2 - left) / w,
    cy: (viewport.h / 2 - top) / h,
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

/** The world engine's overland map of what the party knows: no 5 ft battle grid on it. */
export function isOverland(map: Asset | null | undefined) {
  return !!map?.tags?.some((t) => t.startsWith('lwe-overland'))
}
