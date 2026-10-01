// Shared presentational components. No data fetching here: pages fetch, components render.
import type { ReactNode } from "react";
import { ApiError } from "@/api/client";
import { RAIL_LABEL, humanize } from "@/lib/format";

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, flush, className }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "approve" | "review" | "decline" }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value" style={tone ? { color: `var(--${tone})` } : undefined}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Badge({ value, tone, title }: { value: ReactNode; tone?: string; title?: string }) {
  const cls = tone ?? (typeof value === "string" ? value : "");
  return <span className={`badge ${cls}`} title={title}>{typeof value === "string" ? humanize(value) : value}</span>;
}

export function RailBadge({ rail }: { rail: string }) {
  return (
    <span className="badge" style={{ background: `color-mix(in srgb, var(--rail-${rail}) 14%, transparent)`, color: `var(--rail-${rail})` }}>
      {RAIL_LABEL[rail] ?? rail}
    </span>
  );
}

export function Health({ ok, label }: { ok: boolean; label: string }) {
  return <span className="row" style={{ gap: 6 }}><span className={`dot ${ok ? "up" : "down"}`} />{humanize(label)}</span>;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="alert-box error" role="alert">
      <div className="spread">
        <div>
          <b>{e ? humanize(e.code) : "Something went wrong"}</b>: {e?.message ?? String((error as Error)?.message ?? error)}
          {e?.requestId && <div className="small mono">request {e.requestId}</div>}
        </div>
        {onRetry && <button className="btn small" onClick={onRetry}>Retry</button>}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Loading / error / content switch for a TanStack Query result. */
export function Async<T>({ query, children }: {
  query: { data: T | undefined; isLoading: boolean; error: unknown; refetch: () => unknown };
  children: (data: T) => ReactNode;
}) {
  if (query.isLoading) return <div className="center"><Spinner /></div>;
  if (query.error) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (query.data === undefined) return null;
  return <>{children(query.data)}</>;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label?: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "active" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[];
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function Pagination({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="pagination">
      <span>{from}-{to} of {total.toLocaleString()}</span>
      <div className="row">
        <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
        <button className="btn small" disabled={to >= total} onClick={() => onChange(offset + limit)}>Next</button>
      </div>
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v ?? "-"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Bar({ value, max = 1, color }: { value: number; max?: number; color?: string }) {
  const w = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return <div className="bar"><span style={{ width: `${w}%`, background: color }} /></div>;
}
