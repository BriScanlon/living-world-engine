/**
 * Calibrated grid math.
 *
 * Everything is expressed in *table CSS pixels*. `hexPx` is the physical 1" hex
 * measured flat-to-flat (= the square size for square grids), as calibrated with a
 * real ruler on the glass. Because it is measured in CSS px on the actual table
 * browser, OS display scaling (e.g. 150% on 4K) is already accounted for.
 *
 *   flat-to-flat f  = hexPx
 *   side / radius s = f / sqrt(3)
 *   pointy-top: column step f,       row step 1.5 s, odd rows shifted f/2 right
 *   flat-top:   column step 1.5 s,   row step f,     odd columns shifted f/2 down
 */
import type { GridType } from './types'

export const SQRT3 = Math.sqrt(3)

export interface GridDrawOptions {
  type: GridType
  hexPx: number
  color: string
  /** Stroke width in table *device* px. */
  lineWidth: number
  tableDpr: number
  offsetX: number
  offsetY: number
  showCoords: boolean
  /** Visible area in table CSS px. */
  width: number
  height: number
}

/** Vertex offsets (relative to hex centre) for a hex of flat-to-flat size f. */
export function hexCorners(type: 'hex-pointy' | 'hex-flat', f: number): [number, number][] {
  const s = f / SQRT3
  const start = type === 'hex-pointy' ? -90 : 0
  const pts: [number, number][] = []
  for (let k = 0; k < 6; k++) {
    const a = ((start + 60 * k) * Math.PI) / 180
    pts.push([s * Math.cos(a), s * Math.sin(a)])
  }
  return pts
}

/** Centre of hex (col,row) in offset coordinates. */
export function hexCenter(type: 'hex-pointy' | 'hex-flat', f: number, col: number, row: number): [number, number] {
  const s = f / SQRT3
  if (type === 'hex-pointy') {
    return [col * f + (Math.abs(row) % 2 === 1 ? f / 2 : 0), row * 1.5 * s]
  }
  return [col * 1.5 * s, row * f + (Math.abs(col) % 2 === 1 ? f / 2 : 0)]
}

function mod(a: number, n: number) {
  return ((a % n) + n) % n
}

/**
 * Draw the grid into `ctx`. The caller sets a transform so that 1 unit = 1 table CSS px
 * (for the DM preview that transform includes the preview's down-scale).
 * `unitsPerDevicePx` converts the requested device-px line width into drawing units.
 */
export function drawGrid(ctx: CanvasRenderingContext2D, o: GridDrawOptions, unitsPerDevicePx: number) {
  const f = Math.max(4, o.hexPx)
  ctx.save()
  ctx.strokeStyle = o.color
  ctx.lineWidth = Math.max(o.lineWidth / o.tableDpr, unitsPerDevicePx)
  ctx.lineJoin = 'round'
  ctx.beginPath()

  if (o.type === 'square') {
    const ox = mod(o.offsetX, f)
    const oy = mod(o.offsetY, f)
    for (let x = ox; x <= o.width + 1; x += f) {
      ctx.moveTo(x, 0)
      ctx.lineTo(x, o.height)
    }
    for (let y = oy; y <= o.height + 1; y += f) {
      ctx.moveTo(0, y)
      ctx.lineTo(o.width, y)
    }
    ctx.stroke()
    if (o.showCoords) {
      labelCells(ctx, o, f, (cb) => {
        const c0 = Math.floor(-o.offsetX / f) - 1
        const r0 = Math.floor(-o.offsetY / f) - 1
        for (let r = r0; r * f + o.offsetY < o.height + f; r++)
          for (let c = c0; c * f + o.offsetX < o.width + f; c++) cb(c, r, o.offsetX + (c + 0.5) * f, o.offsetY + (r + 0.5) * f)
      })
    }
    ctx.restore()
    return
  }

  const type = o.type
  const s = f / SQRT3
  const corners = hexCorners(type, f)
  const colStep = type === 'hex-pointy' ? f : 1.5 * s
  const rowStep = type === 'hex-pointy' ? 1.5 * s : f
  // range of cells that can touch the viewport (pad by 2 cells each side)
  const c0 = Math.floor(-o.offsetX / colStep) - 2
  const c1 = Math.ceil((o.width - o.offsetX) / colStep) + 2
  const r0 = Math.floor(-o.offsetY / rowStep) - 2
  const r1 = Math.ceil((o.height - o.offsetY) / rowStep) + 2

  const forEach = (cb: (c: number, r: number, x: number, y: number) => void) => {
    for (let r = r0; r <= r1; r++)
      for (let c = c0; c <= c1; c++) {
        const [hx, hy] = hexCenter(type, f, c, r)
        cb(c, r, hx + o.offsetX, hy + o.offsetY)
      }
  }

  forEach((_c, _r, x, y) => {
    ctx.moveTo(x + corners[0][0], y + corners[0][1])
    for (let k = 1; k < 6; k++) ctx.lineTo(x + corners[k][0], y + corners[k][1])
    ctx.closePath()
  })
  ctx.stroke()
  if (o.showCoords) labelCells(ctx, o, f, forEach)
  ctx.restore()
}

function labelCells(
  ctx: CanvasRenderingContext2D,
  o: GridDrawOptions,
  f: number,
  forEach: (cb: (c: number, r: number, x: number, y: number) => void) => void,
) {
  ctx.fillStyle = o.color
  ctx.font = `600 ${Math.round(f * 0.17)}px ui-monospace, Consolas, monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const dy = o.type === 'square' ? f * 0.32 : f * 0.3
  forEach((c, r, x, y) => {
    if (x < -f || y < -f || x > o.width + f || y > o.height + f) return
    ctx.fillText(`${colLabel(c)}${r + 1}`, x, y + dy)
  })
}

/** 0 -> A, 25 -> Z, 26 -> AA ... negative columns get a minus sign. */
export function colLabel(c: number): string {
  if (c < 0) return `-${colLabel(-c - 1)}`
  let s = ''
  let n = c
  do {
    s = String.fromCharCode(65 + (n % 26)) + s
    n = Math.floor(n / 26) - 1
  } while (n >= 0)
  return s
}

/** Physical pixels per inch implied by a calibration (useful sanity check for the DM). */
export function devicePpi(hexPx: number, dpr: number) {
  return hexPx * dpr
}

/** Expected CSS px per inch for a panel, given its diagonal and resolution. */
export function expectedCssPxPerInch(diagInches: number, resW: number, resH: number, dpr: number) {
  const ppi = Math.hypot(resW, resH) / diagInches
  return ppi / dpr
}
