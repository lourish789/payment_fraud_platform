import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin, type Anchor, type WindowName } from "@/api/endpoints";
import type { Overview } from "@/api/types";
import { AmountOverTime, DecisionDonut, DecisionsOverTime, FlaggedRate } from "@/components/charts";
import { Async, Bar, Card, Health, KV, PageHeader, RailBadge, Segmented, Stat } from "@/components/ui";
import { compactNum, dateTime, duration, int, money, moneyCompact, num, pct, prob } from "@/lib/format";

const WINDOWS: { value: WindowName; label: string }[] = [
  { value: "24h", label: "24h" }, { value: "7d", label: "7d" }, { value: "30d", label: "30d" },
  { value: "90d", label: "90d" }, { value: "all", label: "All" },
];

/** One page that reflects the whole system: traffic and decisions on every rail, the analyst queue,
 * labels and live precision, the agent, receipts, the event pipeline, models and component health. */
export function AdminDashboardPage() {
  const [window, setWindow] = useState<WindowName>("all");
  const [anchor, setAnchor] = useState<Anchor>("now");
  const bucket = window === "24h" || window === "7d" ? "hour" : "day";
  // Hourly buckets need a bounded window; "all" with the default anchor is bucketed by day.
  const overview = useQuery({ queryKey: ["admin", "overview", window, anchor], queryFn: () => admin.overview({ window, anchor }), refetchInterval: 30_000 });
  const series = useQuery({ queryKey: ["admin", "timeseries", window, anchor, bucket], queryFn: () => admin.timeseries({ window, anchor, bucket }), refetchInterval: 60_000 });

  return (
    <>
      <PageHeader
        title="Admin dashboard"
        description={overview.data ? <WindowNote o={overview.data} /> : "System-wide view of every payment rail."}
        actions={<>
          <Segmented label="Time window" value={window} options={WINDOWS} onChange={setWindow} />
          <Segmented label="Anchor" value={anchor} onChange={setAnchor}
                     options={[{ value: "now", label: "Ending now" }, { value: "latest_event", label: "Ending at latest event" }]} />
        </>}
      />
      <Async query={overview}>
        {(o) => (
          <div className="stack">
            <Kpis o={o} />
            <div className="grid grid-3">
              <Card title="Decisions over time" subtitle={`per ${bucket}, stacked`} className="span-2">
                <Async query={series}>{(s) => s.series.length ? <DecisionsOverTime data={s.series} /> : <NoData />}</Async>
              </Card>
              <Card title="Decision mix" subtitle={`${int(o.transactions.total)} decisions`}>
                <DecisionDonut data={o.by_decision} />
                <KV items={[
                  ["Approve", `${int(o.by_decision.approve)} (${pct(o.by_decision.approve / (o.transactions.total || 1))})`],
                  ["Review", `${int(o.by_decision.review)} (${pct(o.by_decision.review / (o.transactions.total || 1))})`],
                  ["Decline", `${int(o.by_decision.decline)} (${pct(o.by_decision.decline / (o.transactions.total || 1))})`],
                ]} />
              </Card>
            </div>
            <div className="grid grid-2">
              <Card title="Volume vs flagged amount"><Async query={series}>{(s) => s.series.length ? <AmountOverTime data={s.series} /> : <NoData />}</Async></Card>
              <Card title="Flag rate" subtitle="(review + decline) / all"><Async query={series}>{(s) => s.series.length ? <FlaggedRate data={s.series} /> : <NoData />}</Async></Card>
            </div>
            <RailsTable o={o} />
            <div className="grid grid-3">
              <CasesCard o={o} />
              <LabelsCard o={o} />
              <AgentCard o={o} />
            </div>
            <div className="grid grid-3">
              <ModelsCard o={o} />
              <PipelineCard o={o} />
              <HealthCard o={o} />
            </div>
          </div>
        )}
      </Async>
    </>
  );
}

function WindowNote({ o }: { o: Overview }) {
  const w = o.window;
  const range = w.from ? `${dateTime(w.from)} to ${dateTime(w.to)}` : `everything up to ${dateTime(w.to)}`;
  return <>Business time {range}{w.anchored_to === "latest_event" && " (window ends at the newest event, for inspecting replayed history)"}.</>;
}

function NoData() {
  return <div className="empty">No transactions in this window.</div>;
}

function Kpis({ o }: { o: Overview }) {
  const t = o.transactions;
  return (
    <div className="grid grid-4">
      <Stat label="Transactions scored" value={compactNum(t.total)} sub={`${moneyCompact(t.amount)} volume`} />
      <Stat label="Flagged (review + decline)" value={pct(t.flag_rate)} sub={`${int(t.flagged)} · ${moneyCompact(t.flagged_amount)}`} tone="review" />
      <Stat label="Open cases" value={int(o.cases.open)} sub={`${moneyCompact(o.cases.open_exposure)} expected loss in queue`} />
      <Stat label="Live precision (flagged)" value={pct(o.labels.precision_flagged)}
            sub={`recall ${pct(o.labels.recall)} on ${int(o.labels.total)} labels`} tone="approve" />
      <Stat label="Scoring latency p95" value={o.latency_ms.p95 === null ? "-" : `${num(o.latency_ms.p95, 1)} ms`}
            sub={`p50 ${num(o.latency_ms.p50, 1)} ms · p99 ${num(o.latency_ms.p99, 1)} ms`} />
      <Stat label="Mean fraud probability" value={prob(t.mean_fraud_probability)} sub={`fraud rate in labels ${pct(o.labels.fraud_rate, 2)}`} />
      <Stat label="Agent ↔ analyst agreement" value={pct(o.agent.analyst_agreement)} sub={`${int(o.agent.compared)} resolved cases compared`} />
      <Stat label="Outbox backlog" value={int(o.events.pending)} tone={o.events.pending > 1000 ? "decline" : undefined}
            sub={o.events.oldest_pending_age_s === null ? "fully relayed" : `oldest ${duration(o.events.oldest_pending_age_s)}`} />
    </div>
  );
}

function RailsTable({ o }: { o: Overview }) {
  const max = Math.max(...o.by_rail.map((r) => r.total), 1);
  return (
    <Card title="Payment rails" subtitle="Every rail goes through the same pipeline and lands in the same queue" flush
          actions={<Link to="/admin/rails" className="btn small">Rail configuration</Link>}>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Rail</th><th className="num">Transactions</th><th style={{ width: "22%" }}>Share</th><th className="num">Volume</th>
            <th className="num">Review</th><th className="num">Decline</th><th className="num">Flag rate</th></tr></thead>
          <tbody>
            {o.by_rail.length === 0 && <tr><td colSpan={7}><NoData /></td></tr>}
            {o.by_rail.map((r) => (
              <tr key={r.rail}>
                <td><RailBadge rail={r.rail} /></td>
                <td className="num">{int(r.total)}</td>
                <td><Bar value={r.total} max={max} color={`var(--rail-${r.rail})`} /></td>
                <td className="num">{money(r.amount)}</td>
                <td className="num">{int(r.review)}</td>
                <td className="num">{int(r.decline)}</td>
                <td className="num">{pct(r.flag_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function CasesCard({ o }: { o: Overview }) {
  const c = o.cases;
  return (
    <Card title="Case queue" actions={<Link to="/cases" className="btn small">Open queue</Link>}>
      <KV items={[
        ["Open", int(c.open)],
        ["Expected loss in queue", money(c.open_exposure)],
        ["Oldest open case", duration(c.oldest_open_age_s)],
        ["Resolved", `${int(c.resolved)} (fraud ${int(c.resolved_fraud)}, legit ${int(c.resolved_legit)})`],
        ["Median time to resolve", duration(c.median_time_to_resolve_s)],
      ]} />
    </Card>
  );
}

function LabelsCard({ o }: { o: Overview }) {
  const l = o.labels;
  return (
    <Card title="Ground truth" subtitle="Analyst resolutions and chargebacks">
      <KV items={[
        ["Labelled transactions", int(l.total)],
        ["Confirmed fraud", `${int(l.fraud)} (${pct(l.fraud_rate, 2)})`],
        ["Precision of flags", pct(l.precision_flagged)],
        ["Recall", pct(l.recall)],
        ...Object.entries(l.by_source).map(([k, v]) => [`From ${k}`, int(v)] as [string, string]),
      ]} />
    </Card>
  );
}

function AgentCard({ o }: { o: Overview }) {
  const a = o.agent;
  const total = Object.values(a.by_status).reduce((x, y) => x + y, 0);
  return (
    <Card title="Investigation agent" subtitle={`provider: ${a.provider}`} actions={<Link to="/investigations" className="btn small">Reports</Link>}>
      <KV items={[
        ["Investigations", int(total)],
        ["By status", Object.entries(a.by_status).map(([k, v]) => `${k} ${v}`).join(" · ") || "-"],
        ["Recommendations", Object.entries(a.by_recommendation).map(([k, v]) => `${k} ${v}`).join(" · ") || "-"],
        ["Agreement with analysts", a.compared ? `${pct(a.analyst_agreement)} of ${a.compared}` : "-"],
        ["LLM tokens", `${compactNum(a.tokens.input)} in · ${compactNum(a.tokens.output)} out`],
        ["Receipts verified", Object.entries(o.receipts).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"],
      ]} />
    </Card>
  );
}

function ModelsCard({ o }: { o: Overview }) {
  const m = o.models.card_test_metrics;
  return (
    <Card title="Card model" actions={<Link to="/models" className="btn small">Registry</Link>}>
      <KV items={[
        ["Champion", <span className="mono">{o.models.champion ?? "none"}</span>],
        ["Challenger (shadow)", <span className="mono">{o.models.challenger ?? "none"}</span>],
        ["Test ROC-AUC", num(m.roc_auc, 3)],
        ["Test PR-AUC", num(m.pr_auc, 3)],
        ["Recall @ 1% FPR", pct(m.recall_at_1pct_fpr)],
        ["Calibration (ECE)", num(m.ece, 4)],
      ]} />
    </Card>
  );
}

function PipelineCard({ o }: { o: Overview }) {
  return (
    <Card title="Event pipeline" subtitle="Transactional outbox → bus → workers" actions={<Link to="/admin/events" className="btn small">Events</Link>}>
      <KV items={[
        ["Events written", int(o.events.total)],
        ["Pending relay", int(o.events.pending)],
        ["Oldest pending", duration(o.events.oldest_pending_age_s)],
      ]} />
      {o.events.pending > 0 && o.events.oldest_pending_age_s !== null && o.events.oldest_pending_age_s > 300 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          A backlog older than a few minutes means no relay is running (expected after an offline replay; in
          production, page the on-call).
        </p>
      )}
    </Card>
  );
}

function HealthCard({ o }: { o: Overview }) {
  return (
    <Card title="Component health" actions={<Link to="/admin/system" className="btn small">System</Link>}>
      <div className="stack" style={{ gap: 8 }}>
        {Object.entries(o.health).map(([k, v]) => <Health key={k} ok={v} label={k} />)}
      </div>
    </Card>
  );
}
