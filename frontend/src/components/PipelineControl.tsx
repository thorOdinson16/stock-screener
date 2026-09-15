import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePipelineStatus, useRetrainModel, useRunMaintenance, useRunPipeline } from "../hooks";
import type { PipelineRun, PipelineStep } from "../types";
import { fmtDateTime } from "../format";

const STEP_LABELS: Record<string, string> = {
  preflight: "Preflight",
  poll: "Poll market data",
  ingest: "Ingest (SeaTunnel)",
  indicators: "Compute indicators",
  score: "Score universe",
  serve: "Publish serving data",
  wait_for_druid: "Wait for Druid",
  retrain: "Retrain models",
  maintenance: "Iceberg maintenance",
};

function stepBadge(state: string): string {
  if (state === "success") return "pos";
  if (state === "failed" || state === "upstream_failed") return "neg";
  if (state === "running") return "active";
  return "muted";
}

function formatDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

function StepRow({ step }: { step: PipelineStep }) {
  const cls = stepBadge(step.state);
  return (
    <div className="step-row">
      <span className={`step-dot ${cls}`} />
      <span className="step-name">{STEP_LABELS[step.id] ?? step.id}</span>
      <span className={`step-state ${cls}`}>{step.state.replace("_", " ")}</span>
    </div>
  );
}

function StatusPanel({ run }: { run: PipelineRun }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!run.active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [run.active]);

  const start = run.start_date ? new Date(run.start_date).getTime() : null;
  const end = run.end_date ? new Date(run.end_date).getTime() : now;
  const elapsed = start ? formatDuration(end - start) : "—";

  return (
    <div className="run-panel">
      <div className="run-panel-head">
        <div>
          <span className={`run-badge ${run.state}`}>{run.state}</span>
          <span className="run-type">{run.run_type} run</span>
        </div>
        <div className="muted">
          {start ? fmtDateTime(run.start_date) : "—"} · {elapsed}
          {run.run_type !== "retrain" && run.conf?.full ? " · full" : ""}
          {" · "}
          <a className="link" href={run.airflow_url} target="_blank" rel="noreferrer">
            open in Airflow ↗
          </a>
        </div>
      </div>
      <div className="steps">
        {run.steps.map((step) => (
          <StepRow key={step.id} step={step} />
        ))}
      </div>
    </div>
  );
}

export default function PipelineControl() {
  const status = usePipelineStatus();
  const runPipeline = useRunPipeline();
  const retrain = useRetrainModel();
  const maintenance = useRunMaintenance();
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  const [limit, setLimit] = useState("");
  const [publishHistory, setPublishHistory] = useState(false);

  const run = status.data;
  const active = run?.active ?? false;
  const busy = active || runPipeline.isPending || retrain.isPending || maintenance.isPending;
  const error = runPipeline.error || retrain.error || maintenance.error;

  // Refresh dashboard data whenever a run completes successfully.
  const previousState = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (run?.state === "success" && previousState.current !== "success" && previousState.current !== undefined) {
      queryClient.invalidateQueries();
    }
    previousState.current = run?.state;
  }, [run?.state, queryClient]);

  const submit = () => {
    runPipeline.mutate(
      {
        full,
        universe_limit: limit.trim() === "" ? null : Number(limit),
        publish_history: publishHistory,
      },
      { onSuccess: () => setOpen(false) }
    );
  };

  return (
    <>
      <div className="run-actions">
        <button className="btn primary" onClick={() => setOpen(true)} disabled={busy}>
          {active ? "Running…" : "Run pipeline"}
        </button>
        <button
          className="btn"
          onClick={() => retrain.mutate()}
          disabled={busy}
          title="Rebuild the training set and retrain the scoring models"
        >
          {retrain.isPending ? "Retraining…" : "Retrain model"}
        </button>
        <button
          className="btn"
          onClick={() => {
            if (window.confirm("Run Iceberg maintenance? This compacts tables and expires old snapshots.")) {
              maintenance.mutate();
            }
          }}
          disabled={busy}
          title="Compact tables, rewrite manifests and expire old snapshots"
        >
          {maintenance.isPending ? "Maintaining…" : "Maintenance"}
        </button>
      </div>

      {busy && <div className="run-hint muted">A run is in progress — this can take a few minutes.</div>}
      {error && <div className="run-error">{(error as Error).message}</div>}

      {run && run.dag_run_id && <StatusPanel run={run} />}

      {open && (
        <div className="modal-overlay" onClick={() => setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Run screening pipeline</h3>
            <div className="field">
              <label>Scope</label>
              <div className="seg">
                <button className={!full ? "active" : ""} onClick={() => setFull(false)}>
                  Quick (quotes only)
                </button>
                <button className={full ? "active" : ""} onClick={() => setFull(true)}>
                  Full (+ fundamentals)
                </button>
              </div>
            </div>
            <div className="field" style={{ marginTop: 12 }}>
              <label>Universe limit (blank = full NIFTY 500)</label>
              <input value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="e.g. 50" />
            </div>
            <label className="toggle" style={{ marginTop: 12 }}>
              <input
                type="checkbox"
                checked={publishHistory}
                onChange={(e) => setPublishHistory(e.target.checked)}
              />
              <span>Also publish daily price history to Druid</span>
            </label>
            <div className="modal-actions">
              <button className="btn" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button className="btn primary" onClick={submit} disabled={runPipeline.isPending}>
                {runPipeline.isPending ? "Starting…" : "Run"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
