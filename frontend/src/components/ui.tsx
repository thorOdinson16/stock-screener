import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { fmtDateTime } from "../format";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = "",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
}) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state">
      <div className="spinner" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="state error">
      <strong>Something went wrong.</strong>
      <code>{message.slice(0, 400)}</code>
    </div>
  );
}

export function EmptyState({ message = "No data." }: { message?: string }) {
  return <div className="state">{message}</div>;
}

export function Updated({ at }: { at: number }) {
  if (!at) return null;
  return <span className="updated" title={new Date(at).toISOString()}>updated {fmtDateTime(at)}</span>;
}

export function QueryView<T>({
  query,
  children,
  empty,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  empty?: (data: T) => boolean;
}) {
  if (query.isLoading) return <Spinner />;
  if (query.isError) return <ErrorState error={query.error} />;
  const data = query.data as T;
  if (data === undefined || data === null) return <EmptyState />;
  if (empty && empty(data)) return <EmptyState />;
  return <>{children(data)}</>;
}
