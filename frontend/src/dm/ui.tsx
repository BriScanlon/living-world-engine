import type { ReactNode } from 'react'

export function Panel({ title, actions, children, className = '' }: { title: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-white/10 bg-slate-900/70 shadow-lg ${className}`}>
      <header className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">{title}</h2>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

type Variant = 'primary' | 'ghost' | 'danger' | 'subtle'
const variants: Record<Variant, string> = {
  primary: 'bg-amber-500 text-slate-950 hover:bg-amber-400 disabled:bg-amber-500/40',
  ghost: 'bg-white/5 text-slate-200 ring-1 ring-white/10 hover:bg-white/10 disabled:opacity-40',
  danger: 'bg-red-600/80 text-white hover:bg-red-500 disabled:opacity-40',
  subtle: 'text-slate-300 hover:bg-white/10 disabled:opacity-40',
}

export function Button({
  variant = 'ghost',
  active,
  className = '',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; active?: boolean }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${
        active ? 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-400/60' : variants[variant]
      } ${className}`}
    />
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 py-1 text-sm text-slate-200">
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? 'bg-amber-500' : 'bg-slate-700'}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-5' : ''}`}
        />
      </button>
    </label>
  )
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 0.01,
  onChange,
  format = (v) => v.toFixed(2),
  disabled,
}: {
  label: ReactNode
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
  disabled?: boolean
}) {
  return (
    <label className={`block py-1 text-sm ${disabled ? 'opacity-40' : ''}`}>
      <div className="mb-1 flex justify-between text-slate-300">
        <span>{label}</span>
        <span className="font-mono text-xs text-slate-400">{format(value)}</span>
      </div>
      <input
        type="range"
        className="w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
    </label>
  )
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: ReactNode }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex w-full rounded-lg bg-slate-800 p-0.5 ring-1 ring-white/10">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
            o.value === value ? 'bg-amber-500 text-slate-950' : 'text-slate-300 hover:bg-white/5'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function ProgressBar({ value, tone = 'amber' }: { value: number; tone?: 'amber' | 'red' | 'green' }) {
  const color = tone === 'red' ? 'bg-red-500' : tone === 'green' ? 'bg-emerald-500' : 'bg-amber-500'
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full ${color} transition-[width] duration-300`} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  )
}
