/**
 * Lightweight procedural ambient effects (Layer 2) on a single 2D canvas.
 * Rendered below full 4K resolution — fog/embers/torchlight are soft by nature,
 * so this keeps a 32" 4K panel at 60 fps even on modest mini-PCs.
 */
import type { FxSettings } from './types'

interface Blob { x: number; y: number; r: number; vx: number; vy: number; a: number }
interface Drop { x: number; y: number; len: number; speed: number }
interface Ember { x: number; y: number; vy: number; phase: number; size: number; life: number; max: number }

const RENDER_SCALE = 0.6 // canvas px per CSS px

function sprite(size: number, inner: string, outer: string) {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  grad.addColorStop(0, inner)
  grad.addColorStop(1, outer)
  g.fillStyle = grad
  g.fillRect(0, 0, size, size)
  return c
}

export class AmbientFx {
  private ctx: CanvasRenderingContext2D
  private raf = 0
  private last = 0
  private w = 0
  private h = 0
  private blobs: Blob[] = []
  private drops: Drop[] = []
  private embers: Ember[] = []
  private fogSprite = sprite(256, 'rgba(220,225,235,0.55)', 'rgba(220,225,235,0)')
  private emberSprite = sprite(64, 'rgba(255,200,90,1)', 'rgba(255,80,0,0)')
  private flicker = 0.5
  private t = 0
  settings: FxSettings | null = null

  constructor(private canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext('2d')!
  }

  resize(cssW: number, cssH: number) {
    this.w = Math.max(1, Math.round(cssW * RENDER_SCALE))
    this.h = Math.max(1, Math.round(cssH * RENDER_SCALE))
    this.canvas.width = this.w
    this.canvas.height = this.h
    this.blobs = []
    this.drops = []
    this.embers = []
  }

  update(settings: FxSettings) {
    this.settings = settings
    const any = Object.values(settings).some((s) => s.enabled && s.intensity > 0)
    if (any && !this.raf) {
      this.last = performance.now()
      this.raf = requestAnimationFrame(this.frame)
    } else if (!any) {
      this.stop()
      this.ctx.clearRect(0, 0, this.w, this.h)
    }
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  private frame = (now: number) => {
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    this.t += dt
    const s = this.settings
    const ctx = this.ctx
    ctx.clearRect(0, 0, this.w, this.h)
    if (s) {
      if (s.torch.enabled) this.drawTorch(s.torch.intensity, dt)
      if (s.fog.enabled) this.drawFog(s.fog.intensity, dt)
      if (s.rain.enabled) this.drawRain(s.rain.intensity, dt)
      if (s.embers.enabled) this.drawEmbers(s.embers.intensity, dt)
    }
    this.raf = requestAnimationFrame(this.frame)
  }

  // ---- drifting fog banks -------------------------------------------------
  private drawFog(intensity: number, dt: number) {
    const target = Math.round(10 + intensity * 26)
    const scale = Math.max(this.w, this.h)
    while (this.blobs.length < target) {
      this.blobs.push({
        x: Math.random() * this.w,
        y: Math.random() * this.h,
        r: scale * (0.12 + Math.random() * 0.22),
        vx: (8 + Math.random() * 18) * (this.w / 1500),
        vy: (Math.random() - 0.5) * 6,
        a: 0.25 + Math.random() * 0.45,
      })
    }
    this.blobs.length = target
    const ctx = this.ctx
    ctx.save()
    for (const b of this.blobs) {
      b.x += b.vx * dt
      b.y += b.vy * dt
      if (b.x - b.r > this.w) b.x = -b.r
      if (b.y - b.r > this.h) b.y = -b.r
      if (b.y + b.r < 0) b.y = this.h + b.r
      ctx.globalAlpha = b.a * (0.25 + intensity * 0.55)
      ctx.drawImage(this.fogSprite, b.x - b.r, b.y - b.r, b.r * 2, b.r * 2)
    }
    ctx.restore()
  }

  // ---- rain streaks -------------------------------------------------------
  private drawRain(intensity: number, dt: number) {
    const target = Math.round(150 + intensity * 900)
    while (this.drops.length < target) {
      this.drops.push({
        x: Math.random() * this.w * 1.2,
        y: Math.random() * this.h,
        len: 10 + Math.random() * 22,
        speed: 700 + Math.random() * 600,
      })
    }
    this.drops.length = target
    const ctx = this.ctx
    const slant = 0.18
    ctx.save()
    ctx.strokeStyle = `rgba(190,210,235,${0.18 + intensity * 0.3})`
    ctx.lineWidth = 1.2
    ctx.beginPath()
    for (const d of this.drops) {
      d.y += d.speed * dt
      d.x -= d.speed * slant * dt
      if (d.y > this.h) {
        d.y = -d.len
        d.x = Math.random() * this.w * 1.2
      }
      ctx.moveTo(d.x, d.y)
      ctx.lineTo(d.x + d.len * slant, d.y - d.len)
    }
    ctx.stroke()
    // darken slightly for overcast feel
    ctx.fillStyle = `rgba(10,20,35,${0.08 + intensity * 0.12})`
    ctx.fillRect(0, 0, this.w, this.h)
    ctx.restore()
  }

  // ---- rising embers ------------------------------------------------------
  private drawEmbers(intensity: number, dt: number) {
    const target = Math.round(30 + intensity * 170)
    while (this.embers.length < target) this.embers.push(this.newEmber(true))
    this.embers.length = target
    const ctx = this.ctx
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    for (let i = 0; i < this.embers.length; i++) {
      const e = this.embers[i]
      e.life += dt
      e.y -= e.vy * dt
      e.x += Math.sin(this.t * 1.7 + e.phase) * 18 * dt
      if (e.life > e.max || e.y < -20) {
        this.embers[i] = this.newEmber(false)
        continue
      }
      const fade = Math.sin((e.life / e.max) * Math.PI)
      ctx.globalAlpha = Math.max(0, fade) * (0.5 + 0.5 * Math.sin(this.t * 9 + e.phase))
      const sz = e.size * (4 + intensity * 2)
      ctx.drawImage(this.emberSprite, e.x - sz / 2, e.y - sz / 2, sz, sz)
    }
    ctx.restore()
  }

  private newEmber(anywhere: boolean): Ember {
    return {
      x: Math.random() * this.w,
      y: anywhere ? Math.random() * this.h : this.h + 10,
      vy: 25 + Math.random() * 60,
      phase: Math.random() * Math.PI * 2,
      size: 0.6 + Math.random() * 1.4,
      life: 0,
      max: 5 + Math.random() * 9,
    }
  }

  // ---- flickering torchlight ---------------------------------------------
  private drawTorch(intensity: number, dt: number) {
    // smoothed random walk -> organic flicker
    const target = 0.5 + (Math.random() - 0.5) * 0.9
    this.flicker += (target - this.flicker) * Math.min(1, dt * 12)
    const f = 0.75 + this.flicker * 0.35 + Math.sin(this.t * 13) * 0.03
    const ctx = this.ctx
    const cx = this.w / 2
    const cy = this.h / 2
    const R = Math.hypot(this.w, this.h) * 0.62
    ctx.save()
    const warm = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * f)
    warm.addColorStop(0, `rgba(255,170,70,${0.10 * intensity * f})`)
    warm.addColorStop(0.55, `rgba(255,120,40,${0.05 * intensity})`)
    warm.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.globalCompositeOperation = 'lighter'
    ctx.fillStyle = warm
    ctx.fillRect(0, 0, this.w, this.h)
    ctx.globalCompositeOperation = 'source-over'
    const dark = ctx.createRadialGradient(cx, cy, R * 0.35 * f, cx, cy, R)
    dark.addColorStop(0, 'rgba(0,0,0,0)')
    dark.addColorStop(1, `rgba(8,4,0,${(0.35 + 0.35 * intensity) * (1.1 - f * 0.3)})`)
    ctx.fillStyle = dark
    ctx.fillRect(0, 0, this.w, this.h)
    ctx.restore()
  }
}
