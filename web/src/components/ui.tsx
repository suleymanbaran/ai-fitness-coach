import { ReactNode } from "react";

/* Shared chart styling so every page reads as one instrument panel. */
export const CHART = {
  axes: { stroke: "#6b7580", fontSize: 11, tickLine: false, axisLine: false } as const,
  grid: { stroke: "#2c3540", strokeDasharray: "3 3", vertical: false } as const,
  tooltip: {
    contentStyle: {
      background: "#161c24",
      border: "1px solid #2c3540",
      borderRadius: 8,
      fontSize: 12,
      color: "#eef2f6",
      boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    },
    labelStyle: { color: "#a7b1bd", fontWeight: 600 },
    cursor: { fill: "rgba(163,230,53,0.05)" },
  } as const,
  colors: {
    lime: "#a3e635",
    cyan: "#22d3ee",
    magenta: "#fb7185",
    violet: "#a78bfa",
    amber: "#fbbf24",
    ember: "#fb923c",
    dim: "#3a4450",
  },
};

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-edge bg-night-2 ${className}`}>
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-5 py-3">
          {title && <h2 className="text-[13px] font-semibold text-mist">{title}</h2>}
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  unit,
  delta,
  deltaGood,
}: {
  label: string;
  value: string;
  unit?: string;
  delta?: string;
  deltaGood?: boolean | null;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-edge bg-night-2 px-4 py-3">
      <div className="bg-grad-primary absolute inset-x-0 top-0 h-0.5 opacity-70" />
      <div className="text-xs font-medium text-ash">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span
          className="font-grotesk text-3xl font-bold tracking-tight text-cloud"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {value}
        </span>
        {unit && <span className="text-sm font-medium text-mist">{unit}</span>}
      </div>
      {delta && (
        <div
          className={`mt-1 text-xs font-medium ${
            deltaGood === null || deltaGood === undefined ? "text-ash" : deltaGood ? "text-lime" : "text-magenta"
          }`}
        >
          {delta}
        </div>
      )}
    </div>
  );
}

type ButtonVariant = "primary" | "accent" | "secondary" | "danger" | "ghost";

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const styles: Record<ButtonVariant, string> = {
    primary: "bg-grad-primary text-night font-bold hover:brightness-110",
    accent: "bg-grad-accent text-white font-semibold hover:brightness-110",
    secondary: "bg-night-3 text-cloud hover:bg-edge border border-edge",
    danger: "bg-magenta/10 text-magenta hover:bg-magenta/20 border border-magenta/30",
    ghost: "text-mist hover:text-cloud hover:bg-white/5",
  };
  return (
    <button
      className={`rounded-lg px-3 py-1.5 text-sm transition-all disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
      {...props}
    />
  );
}

const fieldBase =
  "w-full rounded-lg border border-edge bg-night-3 px-3 py-2 text-sm text-cloud placeholder-ash outline-none focus:border-lime/60 focus:ring-2 focus:ring-lime/20";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${fieldBase} ${props.className ?? ""}`} {...props} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`${fieldBase} ${props.className ?? ""}`} {...props} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${fieldBase} ${props.className ?? ""}`} {...props} />;
}

export function Badge({ children, tone = "zinc" }: { children: ReactNode; tone?: "zinc" | "lime" | "amber" | "red" }) {
  const tones = {
    zinc: "bg-white/5 text-mist",
    lime: "bg-lime/15 text-lime",
    amber: "bg-amber/15 text-amber",
    red: "bg-magenta/15 text-magenta",
  };
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${tones[tone]}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-4 text-center text-sm text-ash">{children}</p>;
}

export function Spinner({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-mist">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-edge border-t-lime" />
      {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return <p className="rounded-lg border border-magenta/30 bg-magenta/10 px-3 py-2 text-sm text-magenta">{message}</p>;
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="relative mb-6 flex flex-wrap items-end justify-between gap-3 pb-4">
      {/* gradient rule under the header — the page's signature line */}
      <div className="bg-grad-primary absolute inset-x-0 bottom-0 h-[3px] rounded-full opacity-80" />
      <div>
        <h1 className="font-grotesk text-[28px] font-bold leading-tight tracking-tight text-cloud">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-mist">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}
