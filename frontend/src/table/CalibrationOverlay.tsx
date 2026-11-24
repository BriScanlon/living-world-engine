import { useEffect, useRef } from 'react'
import { hexCorners } from '../lib/hexGrid'
import type { GridType } from '../lib/types'

interface Props {
  type: GridType
  hexPx: number
  width: number
  height: number
  dpr: number
}

const CELLS = 10

/**
 * High-contrast 10-cell ruler. Lay a physical ruler (or 1" miniature bases) on the
 * glass: the scale from 0 to 10 must measure exactly 10 inches / 254 mm.
 * Pointy-top hexes and squares share flat edges horizontally; flat-top hexes share
 * them vertically, so that ruler is drawn as a column.
 */
export function CalibrationOverlay({ type, hexPx, width, height, dpr }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const c = ref.current
    if (!c || width < 1) return
    c.width = Math.round(width * dpr)
    c.height = Math.round(height * dpr)
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const f = hexPx
    const px = 1 / dpr // one device pixel in CSS px
    const vertical = type === 'hex-flat'
    // header (title + 2 lines) and footer reserve, so text never overlaps the ruler
    const header = Math.max(16, f * 0.35) + f * 0.42 + 2 * f * 0.28 + 12
    const footer = Math.max(16, f * 0.3) + f * 0.4
    // 10 cells on the real 32" panel; fewer on small test screens (whole inches only)
    const fit = vertical ? (height - header - footer) / f : (width - f * 0.5) / f
    const n = Math.max(1, Math.min(CELLS, Math.floor(fit)))
    const span = n * f

    ctx.fillStyle = 'rgba(0,0,0,0.92)'
    ctx.fillRect(0, 0, width, height)

    // origin = the leading flat edge of cell 1
    const ox = vertical ? width / 2 - f * 1.2 : (width - span) / 2
    const oy = vertical ? header + (height - header - footer - span) / 2 : height / 2 - f * 0.9

    // --- the cells -----------------------------------------------------
    ctx.lineWidth = 2 * px * 1.5
    for (let i = 0; i < n; i++) {
      const cx = vertical ? ox : ox + f / 2 + i * f
      const cy = vertical ? oy + f / 2 + i * f : oy
      ctx.beginPath()
      if (type === 'square') {
        ctx.rect(cx - f / 2, cy - f / 2, f, f)
      } else {
        hexCorners(type, f).forEach(([dx, dy], k) => (k ? ctx.lineTo(cx + dx, cy + dy) : ctx.moveTo(cx + dx, cy + dy)))
        ctx.closePath()
      }
      ctx.fillStyle = i % 2 ? '#1f2937' : '#0f172a'
      ctx.fill()
      ctx.strokeStyle = '#ffffff'
      ctx.stroke()
      ctx.fillStyle = '#fde047'
      ctx.font = `700 ${Math.round(f * 0.28)}px ui-monospace, Consolas, monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(String(i + 1), cx, cy)
      if (i === 0) {
        // a 1" miniature base should fit exactly between the flats
        ctx.save()
        ctx.setLineDash([4, 4])
        ctx.strokeStyle = '#22d3ee'
        ctx.beginPath()
        ctx.arc(cx, cy, f / 2, 0, Math.PI * 2)
        ctx.stroke()
        ctx.restore()
      }
    }

    // --- inch scale ----------------------------------------------------
    const gap = f * 0.75
    const sx = vertical ? ox + f * 0.5 + gap : ox
    const sy = vertical ? oy : oy + f * 0.5 + gap
    ctx.strokeStyle = '#fde047'
    ctx.fillStyle = '#fde047'
    ctx.lineWidth = 2 * px * 1.5
    ctx.beginPath()
    if (vertical) {
      ctx.moveTo(sx, sy)
      ctx.lineTo(sx, sy + span)
    } else {
      ctx.moveTo(sx, sy)
      ctx.lineTo(sx + span, sy)
    }
    ctx.stroke()
    ctx.font = `700 ${Math.round(f * 0.22)}px ui-monospace, Consolas, monospace`
    for (let q = 0; q <= n * 4; q++) {
      const t = (q / 4) * f
      const len = q % 4 === 0 ? f * 0.36 : q % 2 === 0 ? f * 0.22 : f * 0.12
      ctx.lineWidth = (q % 4 === 0 ? 2 : 1) * px * 1.5
      ctx.beginPath()
      if (vertical) {
        ctx.moveTo(sx, sy + t)
        ctx.lineTo(sx + len, sy + t)
      } else {
        ctx.moveTo(sx + t, sy)
        ctx.lineTo(sx + t, sy + len)
      }
      ctx.stroke()
      if (q % 4 === 0) {
        ctx.textAlign = vertical ? 'left' : 'center'
        ctx.textBaseline = vertical ? 'middle' : 'top'
        const label = `${q / 4}"`
        if (vertical) ctx.fillText(label, sx + f * 0.45, sy + t)
        else ctx.fillText(label, sx + t, sy + f * 0.42)
      }
    }

    // --- instructions --------------------------------------------------
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillStyle = '#ffffff'
    ctx.font = `700 ${Math.max(18, Math.round(f * 0.3))}px system-ui, sans-serif`
    ctx.fillText('CALIBRATION MODE', width / 2, Math.max(16, f * 0.35))
    ctx.font = `500 ${Math.max(14, Math.round(f * 0.2))}px system-ui, sans-serif`
    ctx.fillStyle = '#cbd5e1'
    const lines = [
      n === CELLS
        ? `Lay a ruler on the glass: the yellow scale from 0" to 10" must measure exactly 10 inches (254 mm).`
        : `Screen too small for 10 cells: the scale from 0" to ${n}" must measure exactly ${n} inches (${(n * 25.4).toFixed(1)} mm).`,
      `A 1" miniature base should sit exactly inside the dashed circle. Adjust with the DM panel buttons.`,
    ]
    lines.forEach((l, i) => ctx.fillText(l, width / 2, Math.max(16, f * 0.35) + f * 0.42 + i * f * 0.28))
    ctx.textBaseline = 'bottom'
    ctx.fillStyle = '#fde047'
    ctx.font = `600 ${Math.max(14, Math.round(f * 0.2))}px ui-monospace, Consolas, monospace`
    const footerText = `1" = ${hexPx.toFixed(2)} CSS px   ·   devicePixelRatio ${dpr}   ·   ${(hexPx * dpr).toFixed(1)} physical px/inch   ·   viewport ${width}×${height}`
    let fontPx = Math.max(14, Math.round(f * 0.2))
    while (fontPx > 9 && ctx.measureText(footerText).width > width - 24) {
      fontPx -= 1
      ctx.font = `600 ${fontPx}px ui-monospace, Consolas, monospace`
    }
    ctx.fillText(footerText, width / 2, height - Math.max(16, f * 0.3))
  }, [type, hexPx, width, height, dpr])

  return <canvas ref={ref} className="layer" />
}
