import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cases, investigations } from "@/api/endpoints";
import type { CaseDetail, Investigation } from "@/api/types";
import { HorizontalBars } from "@/components/charts";
import { Async, Badge, Card, Empty, ErrorState, Json, KV, PageHeader, RailBadge } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";
import { dateTime, humanize, money, num, pct, prob } from "@/lib/format";
import { DecisionCard, PayloadCard } from "@/features/transactions/parts";
import { ReceiptUpload } from "@/features/receipts/ReceiptUpload";

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const q = useQuery({ queryKey: ["case", caseId], queryFn: () => cases.get(caseId) });
  return (
    <Async query={q}>
      {(c) => (
        <>
          <PageHeader
            title={`Case ${c.case_id}`}
            description={<span className="row">
              <RailBadge rail={c.rail} /> <Badge value={c.decision} /> {c.resolution ? <Badge value={c.resolution} /> : <Badge value="open" tone="info" />}
              <span>Expected loss <b>{money(c.priority)}</b></span>
              <span className="muted">· transaction <Link to={`/transactions/${encodeURIComponent(c.transaction_id)}`} className="mono">{c.transaction_id}</Link></span>
            </span>}
            actions={<Link className="btn" to="/cases">Back to queue</Link>}
          />
          <div className="grid grid-3">
            <div className="stack span-2">
              <ExplanationCard c={c} />
              <AgentCard c={c} />
              <PayloadCard payload={c.transaction} />
            </div>
            <div className="stack">
              <ResolveCard c={c} />
              {c.model && <DecisionCard d={c.model} />}
              <ReceiptsCard c={c} />
            </div>
          </div>
        </>
      )}
    </Async>
  );
}

function ExplanationCard({ c }: { c: CaseDetail }) {
  const ex = c.explanation ?? [];
  return (
    <Card title="Why it was flagged"
      subtitle={c.rail === "card" ? "TreeSHAP contributions (log-odds) from the model version that decided" : "Scorecard points that fired (log-odds)"}>
      {ex.length === 0 ? <Empty>No explanation available yet.</Empty> : (
        <>
          <HorizontalBars data={ex.map((e) => ({ name: e.feature, value: e.weight }))} color="decline" format={(v) => num(v, 2)} />
          <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            {ex.map((e) => <li key={e.feature}><span className="mono">{e.feature}</span>: {e.detail}</li>)}
          </ul>
        </>
      )}
    </Card>
  );
}

function ResolveCard({ c }: { c: CaseDetail }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [note, setNote] = useState("");
  const m = useMutation({
    mutationFn: (resolution: "fraud" | "legit") => cases.resolve(c.case_id, resolution, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["case", c.case_id] });
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  if (c.status === "resolved") {
    return (
      <Card title="Resolution">
        <KV items={[
          ["Outcome", <Badge value={c.resolution ?? "-"} />],
          ["By", <span className="mono">{c.resolved_by}</span>],
          ["At", dateTime(c.resolved_at)],
          ["Note", c.resolution_note || "-"],
        ]} />
        <p className="small muted" style={{ marginBottom: 0 }}>Recorded as a label for retraining and live-precision monitoring.</p>
      </Card>
    );
  }
  return (
    <Card title="Resolve" subtitle="Your decision becomes the ground-truth label">
      <div className="stack">
        <textarea className="textarea" rows={3} maxLength={2000} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="row">
          <button className="btn danger" disabled={!can("cases:resolve") || m.isPending} onClick={() => m.mutate("fraud")}>Confirm fraud</button>
          <button className="btn success" disabled={!can("cases:resolve") || m.isPending} onClick={() => m.mutate("legit")}>Mark legitimate</button>
        </div>
        {m.error && <ErrorState error={m.error} />}
      </div>
    </Card>
  );
}

function AgentCard({ c }: { c: CaseDetail }) {
  const qc = useQueryClient();
  const latest = c.investigations[c.investigations.length - 1];
  // The id to follow: the one the POST just returned, else the case's latest. Polling is driven only
  // by the investigation's own status, so it never depends on the case refetch having landed.
  const [requested, setRequested] = useState<string | null>(null);
  const followId = requested ?? latest?.investigation_id;
  const finished = useRef<string | null>(null);
  const live = useQuery({
    queryKey: ["investigation", followId, "trace"],
    queryFn: () => investigations.get(followId!, true),
    enabled: !!followId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "failed" ? false : 1500;
    },
  });
  const status = live.data?.status;
  useEffect(() => {
    // Refresh the case (and queue) once, when the followed run finishes.
    if (followId && (status === "done" || status === "failed") && finished.current !== followId) {
      finished.current = followId;
      if (latest?.status !== status) {
        void qc.invalidateQueries({ queryKey: ["case", c.case_id] });
        void qc.invalidateQueries({ queryKey: ["cases"] });
      }
    }
  }, [followId, status, latest?.status, qc, c.case_id]);
  const run = useMutation({
    mutationFn: () => cases.investigate(c.case_id),
    onSuccess: (r) => {
      finished.current = null;
      setRequested(r.investigation_id);
      void qc.invalidateQueries({ queryKey: ["investigation", r.investigation_id] });
    },
  });
  const inv = live.data ?? latest;
  const active = !!inv && (inv.status === "queued" || inv.status === "running");

  return (
    <Card title="Investigation agent" subtitle="Read-only tools, cited evidence, grounding checked mechanically. It recommends; you decide."
      actions={<button className="btn small primary" onClick={() => run.mutate()} disabled={run.isPending || active}>
        {active || run.isPending ? "Running..." : inv ? "Re-run" : "Investigate"}</button>}>
      {run.error && <ErrorState error={run.error} />}
      {!inv ? <Empty>No investigation yet.</Empty> : <InvestigationView inv={inv} />}
    </Card>
  );
}

export function InvestigationView({ inv }: { inv: Investigation }) {
  const [showTrace, setShowTrace] = useState(false);
  if (inv.status === "queued" || inv.status === "running") return <div className="row"><span className="spinner" /> {humanize(inv.status)}...</div>;
  if (inv.status === "failed" || !inv.report) return <div className="alert-box error">Investigation failed: {inv.error ?? "no report"}</div>;
  const r = inv.report;
  const traceIds = new Map((inv.trace ?? []).map((t) => [t.id, t.tool]));
  return (
    <div className="stack">
      <div className="row">
        <Badge value={r.recommendation} /> <span>confidence <b>{pct(r.confidence, 0)}</b></span>
        <span className="muted">· next: {humanize(r.next_action)}</span>
        <span className="muted small">· {inv.provider}{inv.model ? ` / ${inv.model}` : ""}</span>
      </div>
      <p style={{ margin: 0 }}>{r.summary}</p>
      <div>
        <h3 style={{ marginBottom: 6 }}>Evidence</h3>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {r.evidence.map((e, i) => (
            <li key={i}>{e.claim} <span className="chip mono" title="cited tool call">{traceIds.get(e.tool_call_id) ?? e.tool_call_id}</span></li>
          ))}
        </ol>
      </div>
      {r.grounding && (
        <div className="small muted">
          Grounding: {pct(r.grounding.citation_valid_rate, 0)} of citations point at a real tool call,
          {" "}{pct(r.grounding.grounded_rate, 0)} of claims have every number present in the cited output.
          {" "}Tokens: {inv.tokens.input.toLocaleString()} in / {inv.tokens.output.toLocaleString()} out.
        </div>
      )}
      {inv.trace && (
        <div>
          <button className="btn small ghost" onClick={() => setShowTrace((s) => !s)}>{showTrace ? "Hide" : "Show"} tool trace ({inv.trace.length} calls)</button>
          {showTrace && <div className="stack" style={{ marginTop: 8 }}>
            {inv.trace.map((t) => (
              <details key={t.id}>
                <summary><span className="mono">{t.tool}</span> <span className="small muted mono">{t.id}</span></summary>
                <Json value={{ input: t.input, output: t.output }} />
              </details>
            ))}
          </div>}
        </div>
      )}
    </div>
  );
}

function ReceiptsCard({ c }: { c: CaseDetail }) {
  const qc = useQueryClient();
  return (
    <Card title="Proof of payment" subtitle="OCR → ledger reconciliation → image forensics">
      <div className="stack">
        {c.receipts.length === 0 ? <span className="muted small">No receipts attached.</span> : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {c.receipts.map((r) => <li key={r.receipt_id}><Badge value={r.verdict} /> <span className="mono small">{r.receipt_id}</span></li>)}
          </ul>
        )}
        <ReceiptUpload caseId={c.case_id} defaultReference={c.transaction_id}
          onDone={() => qc.invalidateQueries({ queryKey: ["case", c.case_id] })} compact />
        {c.model && <span className="small muted">Model probability {prob(c.model.fraud_probability)}</span>}
      </div>
    </Card>
  );
}
