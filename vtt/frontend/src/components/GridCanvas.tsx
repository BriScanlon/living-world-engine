import { useEffect, useRef } from 'react'
import { drawGrid } from '../lib/hexGrid'
import type { GridSettings, TableViewport } from '../lib/types'

interface Props {
  grid: GridSettings
  hexPx: number
  /** The real table's viewport — the coordinate system the grid is defined in. */
  table: TableViewport
  /** This element's CSS size and the local devicePixelRatio. */
  width: number
  height: number
  dpr: number
}

/**
 * Layer 3: calibrated grid. Strokes are drawn opaque and the whole canvas gets
 * the opacity, so overlapping hex edges never double up in brightness.
 */
export function GridCanvas({ grid, hexPx, table, width, height, dpr }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || width < 1 || height < 1) return
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
    const ctx = canvas.getContext('2d')!
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    if (!grid.visible) return
    // drawing units = table CSS px
    const viewScale = width / table.w
    const k = dpr * viewScale
    ctx.setTransform(k, 0, 0, k, 0, 0)
    drawGrid(
      ctx,
      {
        type: grid.type,
        hexPx,
        color: grid.color,
        lineWidth: grid.lineWidth,
        tableDpr: table.dpr,
        offsetX: grid.offsetX,
        offsetY: grid.offsetY,
        showCoords: grid.showCoords,
        width: table.w,
        height: table.h,
      },
      1 / k,
    )
  }, [grid, hexPx, table.w, table.h, table.dpr, width, height, dpr])

  return <canvas ref={ref} className="layer" style={{ opacity: grid.visible ? grid.opacity : 0 }} />
}
