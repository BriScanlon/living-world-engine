import { ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ children, className, title, actions, subtitle }: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={cx("rounded-xl border border-ink-700/70 bg-ink-900/80 shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-ink-800 px-4 py-3">
          <div>
            {title && <h2 className="font-display text-lg text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-ink-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Tone = "neutral" | "ember" | "green" | "red" | "blue" | "violet" | "slate";
const TONES: Record<Tone, string> = {
  neutral: "bg-ink-800 text-ink-200 ring-ink-600",
  ember: "bg-ember-500/15 text-ember-300 ring-ember-500/40",
  green: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  red: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
  blue: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  violet: "bg-violet-500/15 text-violet-300 ring-violet-500/40",
  slate: "bg-ink-800/60 text-ink-400 ring-ink-700",
};

export function Badge({ children, tone = "neutral", className, title }: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
  title?: string;
}) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Button({ children, onClick, variant = "secondary", disabled, className, type = "button", title }: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
  title?: string;
}) {
  const v = {
    primary: "bg-ember-500 text-ink-950 hover:bg-ember-400 font-semibold",
    secondary: "bg-ink-800 text-ink-100 ring-1 ring-ink-600 hover:bg-ink-700",
    danger: "bg-rose-600/20 text-rose-200 ring-1 ring-rose-500/50 hover:bg-rose-600/35",
    ghost: "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
  }[variant];
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-40", v, className)}
    >
      {children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-400">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-ink-600 bg-ink-950 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-400 focus:border-ember-500 focus:outline-none focus:ring-1 focus:ring-ember-500";

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-ember-400", className)} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-sm text-ink-400">{children}</div>;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</div>;
}

/** Trust on -100..100 as a centred bar. */
export function TrustBar({ value, ghost }: { value: number | null; ghost?: number | null }) {
  const pct = (v: number) => Math.min(50, (Math.abs(v) / 100) * 50);
  const bar = (v: number | null, cls: string) =>
    v === null ? null : (
      <span
        className={cx("absolute top-0 h-full rounded-sm", cls)}
        style={v >= 0 ? { left: "50%", width: `${pct(v)}%` } : { right: "50%", width: `${pct(v)}%` }}
      />
    );
  return (
    <span className="relative inline-block h-2 w-28 overflow-hidden rounded-sm bg-ink-800 align-middle">
      {bar(ghost ?? null, "bg-ink-600/70")}
      {bar(value, value !== null && value < 0 ? "bg-rose-500/80" : "bg-emerald-500/80")}
      <span className="absolute left-1/2 top-0 h-full w-px bg-ink-400/60" />
    </span>
  );
}

/** Progress clock: filled segments as a pie. */
export function Clock({ filled, segments, prev, size = 28 }: { filled: number; segments: number; prev?: number | null; size?: number }) {
  const r = size / 2 - 2;
  const c = size / 2;
  const wedge = (i: number) => {
    const a0 = (i / segments) * 2 * Math.PI - Math.PI / 2;
    const a1 = ((i + 1) / segments) * 2 * Math.PI - Math.PI / 2;
    return `M${c},${c} L${c + r * Math.cos(a0)},${c + r * Math.sin(a0)} A${r},${r} 0 0 1 ${c + r * Math.cos(a1)},${c + r * Math.sin(a1)} Z`;
  };
  return (
    <svg width={size} height={size} className="shrink-0">
      {Array.from({ length: segments }, (_, i) => (
        <path
          key={i}
          d={wedge(i)}
          className={i < (prev ?? 0) ? "fill-ember-600" : i < filled ? "fill-ember-300" : "fill-ink-800"}
          stroke="var(--color-ink-950)"
          strokeWidth={1.5}
        />
      ))}
    </svg>
  );
}

export const accuracyTone = (a: string): Tone => (a === "True" ? "green" : a === "False" ? "red" : "ember");
export const visibilityTone = (v: string): Tone => (v === "public" ? "blue" : v === "leaked" ? "violet" : "slate");

export function signed(n: number) {
  return n > 0 ? `+${n}` : `${n}`;
}
