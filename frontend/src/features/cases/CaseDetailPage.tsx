import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cases, investigations } from "@/api/endpoints";
import type { CaseDetail, Investigation } from "@/api/types";
import { HorizontalBars } from "@/components/charts";
import { Amount, Async, Badge, Card, Code, Empty, ErrorState, Json, KV, PageHeader, RailBadge } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";
import { useT } from "@/i18n";
import { dateTime, num, pct, prob, usd } from "@/lib/format";
import { DecisionCard, PayloadCard } from "@/features/transactions/parts";
import { ReceiptUpload } from "@/features/receipts/ReceiptUpload";

export function CaseDetailPage() {
  const t = useT();
  const { caseId = "" } = useParams();
  const q = useQuery({ queryKey: ["case", caseId], queryFn: () => cases.get(caseId) });
  return (
    <Async query={q}>
      {(c) => (
        <>
          <PageHeader
            title={t("Case {id}", { id: c.case_id })}
            description={<span className="row">
              <RailBadge rail={c.rail} /> <Badge value={c.decision} /> {c.resolution ? <Badge value={c.resolution} /> : <Badge value="open" tone="info" />}
              {c.money && <span>{t("Amount")} <b><Amount amount={c.money.amount} currency={c.money.currency} amountUsd={c.money.amount_usd} /></b></span>}
              <span>{t("Expected loss")} <b>{usd(c.priority)}</b></span>
              <span className="muted">· {t("transaction")} <Link to={`/transactions/${encodeURIComponent(c.transaction_id)}`} className="mono">{c.transaction_id}</Link></span>
            </span>}
            actions={<Link className="btn" to="/cases">{t("Back to queue")}</Link>}
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
  const t = useT();
  const ex = c.explanation ?? [];
  return (
    <Card title={t("Why it was flagged")}
      subtitle={c.rail === "card" ? t("TreeSHAP contributions (log-odds) from the model version that decided") : t("Scorecard points that fired (log-odds)")}>
      {ex.length === 0 ? <Empty>{t("No explanation available yet.")}</Empty> : (
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
  const t = useT();
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
      <Card title={t("Resolution")}>
        <KV items={[
          [t("Outcome"), <Badge value={c.resolution ?? "-"} />],
          [t("By"), <span className="mono">{c.resolved_by}</span>],
          [t("At"), dateTime(c.resolved_at)],
          [t("Note"), c.resolution_note || "-"],
        ]} />
        <p className="small muted" style={{ marginBottom: 0 }}>{t("Recorded as a label for retraining and live-precision monitoring.")}</p>
      </Card>
    );
  }
  return (
    <Card title={t("Resolve")} subtitle={t("Your decision becomes the ground-truth label")}>
      <div className="stack">
        <textarea className="textarea" rows={3} maxLength={2000} placeholder={t("Note (optional)")} value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="row">
          <button className="btn danger" disabled={!can("cases:resolve") || m.isPending} onClick={() => m.mutate("fraud")}>{t("Confirm fraud")}</button>
          <button className="btn success" disabled={!can("cases:resolve") || m.isPending} onClick={() => m.mutate("legit")}>{t("Mark legitimate")}</button>
        </div>
        {m.error && <ErrorState error={m.error} />}
      </div>
    </Card>
  );
}

function AgentCard({ c }: { c: CaseDetail }) {
  const t = useT();
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
    <Card title={t("Investigation agent")} subtitle={t("Read-only tools, cited evidence, grounding checked mechanically. It recommends; you decide.")}
      actions={<button className="btn small primary" onClick={() => run.mutate()} disabled={run.isPending || active}>
        {active || run.isPending ? t("Running...") : inv ? t("Re-run") : t("Investigate")}</button>}>
      {run.error && <ErrorState error={run.error} />}
      {!inv ? <Empty>{t("No investigation yet.")}</Empty> : <InvestigationView inv={inv} />}
    </Card>
  );
}

export function InvestigationView({ inv }: { inv: Investigation }) {
  const t = useT();
  const [showTrace, setShowTrace] = useState(false);
  if (inv.status === "queued" || inv.status === "running") return <div className="row"><span className="spinner" /> <Code value={inv.status} />...</div>;
  if (inv.status === "failed" || !inv.report) return <div className="alert-box error">{t("Investigation failed: {error}", { error: inv.error ?? t("no report") })}</div>;
  const r = inv.report;
  const traceIds = new Map((inv.trace ?? []).map((s) => [s.id, s.tool]));
  return (
    <div className="stack">
      <div className="row">
        <Badge value={r.recommendation} /> <span>{t("confidence")} <b>{pct(r.confidence, 0)}</b></span>
        <span className="muted">· {t("next")}: <Code value={r.next_action} /></span>
        <span className="muted small">· {inv.provider}{inv.model ? ` / ${inv.model}` : ""}</span>
      </div>
      <p style={{ margin: 0 }}>{r.summary}</p>
      <div>
        <h3 style={{ marginBottom: 6 }}>{t("Evidence")}</h3>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {r.evidence.map((e, i) => (
            <li key={i}>{e.claim} <span className="chip mono" title={t("cited tool call")}>{traceIds.get(e.tool_call_id) ?? e.tool_call_id}</span></li>
          ))}
        </ol>
      </div>
      {r.grounding && (
        <div className="small muted">
          {t("Grounding: {citations} of citations point at a real tool call, {grounded} of claims have every number present in the cited output. Tokens: {tin} in / {tout} out.", {
            citations: pct(r.grounding.citation_valid_rate, 0), grounded: pct(r.grounding.grounded_rate, 0),
            tin: inv.tokens.input.toLocaleString(), tout: inv.tokens.output.toLocaleString(),
          })}
        </div>
      )}
      {inv.trace && (
        <div>
          <button className="btn small ghost" onClick={() => setShowTrace((s) => !s)}>
            {showTrace ? t("Hide tool trace ({n} calls)", { n: inv.trace.length }) : t("Show tool trace ({n} calls)", { n: inv.trace.length })}
          </button>
          {showTrace && <div className="stack" style={{ marginTop: 8 }}>
            {inv.trace.map((s) => (
              <details key={s.id}>
                <summary><span className="mono">{s.tool}</span> <span className="small muted mono">{s.id}</span></summary>
                <Json value={{ input: s.input, output: s.output }} />
              </details>
            ))}
          </div>}
        </div>
      )}
    </div>
  );
}

function ReceiptsCard({ c }: { c: CaseDetail }) {
  const t = useT();
  const qc = useQueryClient();
  return (
    <Card title={t("Proof of payment")} subtitle={t("OCR → ledger reconciliation → image forensics")}>
      <div className="stack">
        {c.receipts.length === 0 ? <span className="muted small">{t("No receipts attached.")}</span> : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {c.receipts.map((r) => <li key={r.receipt_id}><Badge value={r.verdict} /> <span className="mono small">{r.receipt_id}</span></li>)}
          </ul>
        )}
        <ReceiptUpload caseId={c.case_id} defaultReference={c.transaction_id}
          onDone={() => qc.invalidateQueries({ queryKey: ["case", c.case_id] })} compact />
        {c.model && <span className="small muted">{t("Model probability {p}", { p: prob(c.model.fraud_probability) })}</span>}
      </div>
    </Card>
  );
}
