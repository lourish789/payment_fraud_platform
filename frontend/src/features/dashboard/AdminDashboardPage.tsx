import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin, type Anchor, type WindowName } from "@/api/endpoints";
import type { Overview } from "@/api/types";
import { AmountOverTime, DecisionDonut, DecisionsOverTime, FlaggedRate } from "@/components/charts";
import { Async, Bar, Card, Health, KV, PageHeader, RailBadge, Segmented, Stat } from "@/components/ui";
import { useT } from "@/i18n";
import { codeLabel } from "@/i18n/labels";
import { compactNum, dateTime, displayCurrency, duration, int, num, pct, prob, usd, usdCompact } from "@/lib/format";

/** One page that reflects the whole system: traffic and decisions on every rail, the analyst queue,
 * labels and live precision, the agent, receipts, the event pipeline, models and component health.
 * Money on this page is computed in USD by the API and shown in the viewer's display currency. */
export function AdminDashboardPage() {
  const t = useT();
  const [window, setWindow] = useState<WindowName>("all");
  const [anchor, setAnchor] = useState<Anchor>("now");
  const bucket = window === "24h" || window === "7d" ? "hour" : "day";
  // Hourly buckets need a bounded window; "all" with the default anchor is bucketed by day.
  const overview = useQuery({ queryKey: ["admin", "overview", window, anchor], queryFn: () => admin.overview({ window, anchor }), refetchInterval: 30_000 });
  const series = useQuery({ queryKey: ["admin", "timeseries", window, anchor, bucket], queryFn: () => admin.timeseries({ window, anchor, bucket }), refetchInterval: 60_000 });
  const windows: { value: WindowName; label: string }[] = [
    { value: "24h", label: "24h" }, { value: "7d", label: "7d" }, { value: "30d", label: "30d" },
    { value: "90d", label: "90d" }, { value: "all", label: t("All") },
  ];

  return (
    <>
      <PageHeader
        title={t("Admin dashboard")}
        description={overview.data ? <WindowNote o={overview.data} /> : t("System-wide view of every payment rail.")}
        actions={<>
          <Segmented label={t("Time window")} value={window} options={windows} onChange={setWindow} />
          <Segmented label={t("Anchor")} value={anchor} onChange={setAnchor}
                     options={[{ value: "now", label: t("Ending now") }, { value: "latest_event", label: t("Ending at latest event") }]} />
        </>}
      />
      <Async query={overview}>
        {(o) => (
          <div className="stack">
            <Kpis o={o} />
            <div className="grid grid-3">
              <Card title={t("Decisions over time")} subtitle={bucket === "hour" ? t("per hour, stacked") : t("per day, stacked")} className="span-2">
                <Async query={series}>{(s) => s.series.length ? <DecisionsOverTime data={s.series} /> : <NoData />}</Async>
              </Card>
              <Card title={t("Decision mix")} subtitle={t("{n} decisions", { n: int(o.transactions.total) })}>
                <DecisionDonut data={o.by_decision} />
                <KV items={(["approve", "review", "decline"] as const).map((k) => [
                  t(codeLabel(k)), `${int(o.by_decision[k])} (${pct(o.by_decision[k] / (o.transactions.total || 1))})`,
                ])} />
              </Card>
            </div>
            <div className="grid grid-2">
              <Card title={t("Volume vs flagged amount")} subtitle={t("in {currency}", { currency: displayCurrency() })}>
                <Async query={series}>{(s) => s.series.length ? <AmountOverTime data={s.series} /> : <NoData />}</Async>
              </Card>
              <Card title={t("Flag rate")} subtitle={t("(review + decline) / all")}><Async query={series}>{(s) => s.series.length ? <FlaggedRate data={s.series} /> : <NoData />}</Async></Card>
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
  const t = useT();
  const w = o.window;
  const range = w.from ? t("Business time {from} to {to}.", { from: dateTime(w.from), to: dateTime(w.to) })
    : t("Business time: everything up to {to}.", { to: dateTime(w.to) });
  return <>{range}{w.anchored_to === "latest_event" && ` ${t("The window ends at the newest event, for inspecting replayed history.")}`}</>;
}

function NoData() {
  const t = useT();
  return <div className="empty">{t("No transactions in this window.")}</div>;
}

function Kpis({ o }: { o: Overview }) {
  const t = useT();
  const x = o.transactions;
  return (
    <div className="grid grid-4">
      <Stat label={t("Transactions scored")} value={compactNum(x.total)} sub={t("{amount} volume", { amount: usdCompact(x.amount) })} />
      <Stat label={t("Flagged (review + decline)")} value={pct(x.flag_rate)} sub={`${int(x.flagged)} · ${usdCompact(x.flagged_amount)}`} tone="review" />
      <Stat label={t("Open cases")} value={int(o.cases.open)} sub={t("{amount} expected loss in queue", { amount: usdCompact(o.cases.open_exposure) })} />
      <Stat label={t("Live precision (flagged)")} value={pct(o.labels.precision_flagged)}
            sub={t("recall {recall} on {n} labels", { recall: pct(o.labels.recall), n: int(o.labels.total) })} tone="approve" />
      <Stat label={t("Scoring latency p95")} value={o.latency_ms.p95 === null ? "-" : `${num(o.latency_ms.p95, 1)} ms`}
            sub={`p50 ${num(o.latency_ms.p50, 1)} ms · p99 ${num(o.latency_ms.p99, 1)} ms`} />
      <Stat label={t("Mean fraud probability")} value={prob(x.mean_fraud_probability)} sub={t("fraud rate in labels {rate}", { rate: pct(o.labels.fraud_rate, 2) })} />
      <Stat label={t("Agent ↔ analyst agreement")} value={pct(o.agent.analyst_agreement)} sub={t("{n} resolved cases compared", { n: int(o.agent.compared) })} />
      <Stat label={t("Outbox backlog")} value={int(o.events.pending)} tone={o.events.pending > 1000 ? "decline" : undefined}
            sub={o.events.oldest_pending_age_s === null ? t("fully relayed") : t("oldest {age}", { age: duration(o.events.oldest_pending_age_s) })} />
    </div>
  );
}

function RailsTable({ o }: { o: Overview }) {
  const t = useT();
  const max = Math.max(...o.by_rail.map((r) => r.total), 1);
  return (
    <Card title={t("Payment rails")} subtitle={t("Every rail goes through the same pipeline and lands in the same queue")} flush
          actions={<Link to="/admin/rails" className="btn small">{t("Rail configuration")}</Link>}>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>{t("Rail")}</th><th className="num">{t("Transactions")}</th><th style={{ width: "22%" }}>{t("Share")}</th><th className="num">{t("Volume")}</th>
            <th className="num">{t("Review")}</th><th className="num">{t("Decline")}</th><th className="num">{t("Flag rate")}</th></tr></thead>
          <tbody>
            {o.by_rail.length === 0 && <tr><td colSpan={7}><NoData /></td></tr>}
            {o.by_rail.map((r) => (
              <tr key={r.rail}>
                <td><RailBadge rail={r.rail} /></td>
                <td className="num">{int(r.total)}</td>
                <td><Bar value={r.total} max={max} color={`var(--rail-${r.rail})`} /></td>
                <td className="num">{usd(r.amount)}</td>
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
  const t = useT();
  const c = o.cases;
  return (
    <Card title={t("Case queue")} actions={<Link to="/cases" className="btn small">{t("Open queue")}</Link>}>
      <KV items={[
        [t("Open"), int(c.open)],
        [t("Expected loss in queue"), usd(c.open_exposure)],
        [t("Oldest open case"), duration(c.oldest_open_age_s)],
        [t("Resolved"), t("{n} (fraud {fraud}, legit {legit})", { n: int(c.resolved), fraud: int(c.resolved_fraud), legit: int(c.resolved_legit) })],
        [t("Median time to resolve"), duration(c.median_time_to_resolve_s)],
      ]} />
    </Card>
  );
}

function LabelsCard({ o }: { o: Overview }) {
  const t = useT();
  const l = o.labels;
  return (
    <Card title={t("Ground truth")} subtitle={t("Analyst resolutions and chargebacks")}>
      <KV items={[
        [t("Labelled transactions"), int(l.total)],
        [t("Confirmed fraud"), `${int(l.fraud)} (${pct(l.fraud_rate, 2)})`],
        [t("Precision of flags"), pct(l.precision_flagged)],
        [t("Recall"), pct(l.recall)],
        ...Object.entries(l.by_source).map(([k, v]) => [t("From {source}", { source: t(codeLabel(k)) }), int(v)] as [string, string]),
      ]} />
    </Card>
  );
}

function AgentCard({ o }: { o: Overview }) {
  const t = useT();
  const a = o.agent;
  const total = Object.values(a.by_status).reduce((x, y) => x + y, 0);
  const counts = (m: Record<string, number>) => Object.entries(m).map(([k, v]) => `${t(codeLabel(k))} ${v}`).join(" · ");
  return (
    <Card title={t("Investigation agent")} subtitle={t("provider: {provider}", { provider: a.provider })} actions={<Link to="/investigations" className="btn small">{t("Reports")}</Link>}>
      <KV items={[
        [t("Investigations"), int(total)],
        [t("By status"), counts(a.by_status) || "-"],
        [t("Recommendations"), counts(a.by_recommendation) || "-"],
        [t("Agreement with analysts"), a.compared ? t("{pct} of {n}", { pct: pct(a.analyst_agreement), n: a.compared }) : "-"],
        [t("LLM tokens"), t("{tin} in · {tout} out", { tin: compactNum(a.tokens.input), tout: compactNum(a.tokens.output) })],
        [t("Receipts verified"), counts(o.receipts) || t("none")],
      ]} />
    </Card>
  );
}

function ModelsCard({ o }: { o: Overview }) {
  const t = useT();
  const m = o.models.card_test_metrics;
  return (
    <Card title={t("Card model")} actions={<Link to="/models" className="btn small">{t("Registry")}</Link>}>
      <KV items={[
        [t("Champion"), <span className="mono">{o.models.champion ?? t("none")}</span>],
        [t("Challenger (shadow)"), <span className="mono">{o.models.challenger ?? t("none")}</span>],
        [t("Test ROC-AUC"), num(m.roc_auc, 3)],
        [t("Test PR-AUC"), num(m.pr_auc, 3)],
        [t("Recall @ 1% FPR"), pct(m.recall_at_1pct_fpr)],
        [t("Calibration (ECE)"), num(m.ece, 4)],
      ]} />
    </Card>
  );
}

function PipelineCard({ o }: { o: Overview }) {
  const t = useT();
  return (
    <Card title={t("Event pipeline")} subtitle={t("Transactional outbox → bus → workers")} actions={<Link to="/admin/events" className="btn small">{t("Events")}</Link>}>
      <KV items={[
        [t("Events written"), int(o.events.total)],
        [t("Pending relay"), int(o.events.pending)],
        [t("Oldest pending"), duration(o.events.oldest_pending_age_s)],
      ]} />
      {o.events.pending > 0 && o.events.oldest_pending_age_s !== null && o.events.oldest_pending_age_s > 300 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          {t("A backlog older than a few minutes means no relay is running (expected after an offline replay; in production, page the on-call).")}
        </p>
      )}
    </Card>
  );
}

function HealthCard({ o }: { o: Overview }) {
  const t = useT();
  return (
    <Card title={t("Component health")} actions={<Link to="/admin/system" className="btn small">{t("System")}</Link>}>
      <div className="stack" style={{ gap: 8 }}>
        {Object.entries(o.health).map(([k, v]) => <Health key={k} ok={v} label={k} />)}
      </div>
    </Card>
  );
}
