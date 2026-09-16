import { useApi, useCollectOps } from "../hooks";
import type { OpsSnapshot } from "../types";
import { fmtCompact, fmtDateTime, fmtNum } from "../format";
import { Card, EmptyState, ErrorState, Spinner, StatCard, Updated } from "../components/ui";

function fmtBytes(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return `${fmtCompact(value)} B`;
}

function airflowRuns(snapshot: OpsSnapshot): Record<string, { state?: string | null; end_date?: string | null } | null> {
  const airflow = snapshot.airflow as Record<string, { state?: string | null; end_date?: string | null } | null>;
  return airflow && typeof airflow === "object" ? airflow : {};
}

export default function Ops() {
  const ops = useApi<OpsSnapshot>(["ops"], "/ops");
  const collect = useCollectOps();

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Operations</h1>
          <p className="page-sub">Kafka lag · Druid · HDFS · Airflow pipeline</p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <button className="btn" onClick={() => collect.mutate()} disabled={collect.isPending}>
            {collect.isPending ? "Collecting…" : "Collect now"}
          </button>
          <Updated at={ops.dataUpdatedAt} />
        </div>
      </div>

      {collect.isError && <ErrorState error={collect.error} />}
      {ops.isLoading && <Spinner />}
      {ops.isError && (
        <Card title="No snapshots yet" subtitle="Run the collector to populate this view">
          <EmptyState message="monitoring/collect_metrics.py has not produced a snapshot." />
        </Card>
      )}

      {ops.data && (
        <div className="grid" style={{ gap: 16 }}>
          <div className="stat-row">
            <StatCard label="Kafka consumer lag" value={fmtNum(ops.data.kafka.total_lag, 0)}
              sub={ops.data.kafka.error ? "unavailable" : "messages"} />
            <StatCard label="Druid query latency" value={`${fmtNum(ops.data.druid.segment_query_latency_ms, 0)} ms`} />
            <StatCard label="HDFS live datanodes" value={ops.data.hdfs.live_datanodes ?? "—"}
              sub={`dead: ${ops.data.hdfs.dead_datanodes ?? "—"}`} />
            <StatCard label="HDFS remaining" value={fmtBytes(ops.data.hdfs.remaining_bytes)}
              sub={`of ${fmtBytes(ops.data.hdfs.capacity_bytes)}`} />
          </div>

          <Card title="Druid segments" subtitle={`screener latest: ${fmtDateTime(ops.data.druid.screener_latest)}`}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Datasource</th><th className="num">Active segments</th></tr>
                </thead>
                <tbody>
                  {Object.entries(ops.data.druid.segments ?? {}).map(([ds, n]) => (
                    <tr key={ds}><td>{ds}</td><td className="num mono">{n}</td></tr>
                  ))}
                  {Object.keys(ops.data.druid.segments ?? {}).length === 0 && (
                    <tr><td colSpan={2}>—</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Pipeline runs" subtitle={`snapshot ${fmtDateTime(ops.data.collected_at)}`}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>DAG</th><th>state</th><th>finished</th></tr>
                </thead>
                <tbody>
                  {Object.entries(airflowRuns(ops.data)).map(([dag, run]) => (
                    <tr key={dag}>
                      <td>{dag}</td>
                      <td>{run?.state ?? "—"}</td>
                      <td>{fmtDateTime(run?.end_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
