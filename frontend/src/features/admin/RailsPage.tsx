import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import type { RailOut } from "@/api/types";
import { Async, Badge, Card, KV, PageHeader, RailBadge } from "@/components/ui";
import { useT } from "@/i18n";
import { int, num, pct, usd } from "@/lib/format";

export function RailsPage() {
  const t = useT();
  const q = useQuery({ queryKey: ["admin", "rails"], queryFn: admin.rails });
  return (
    <>
      <PageHeader title={t("Payment rails")}
        description={t("One scoring pipeline, one scorer per rail. Cards use the trained model; bank transfer and mobile money use transparent scorecards (log-odds priors) until labels accumulate; crypto adds sanctions screening and on-chain intelligence.")} />
      <Async query={q}>{(rails) => <div className="stack">{rails.map((r) => <RailCard key={r.rail} r={r} />)}</div>}</Async>
    </>
  );
}

function RailCard({ r }: { r: RailOut }) {
  const t = useT();
  const d = r.details as Record<string, any>;
  return (
    <Card title={<span className="row"><RailBadge rail={r.rail} /> <span className="mono small">{r.version ?? "-"}</span></span>}
      actions={<span className="row">{r.enabled ? <Badge value="enabled" tone="approve" /> : <Badge value="disabled" tone="decline" />}<Badge value={r.engine} tone="info" /></span>}>
      <div className="grid grid-3">
        <KV items={[
          [t("Transactions (all time)"), int(r.stats.total)],
          [t("Review / decline"), `${int(r.stats.review)} / ${int(r.stats.decline)}`],
          [t("Flag rate"), pct(r.stats.flag_rate)],
          [t("Mean latency"), r.stats.mean_latency_ms === null ? "-" : `${num(r.stats.mean_latency_ms, 2)} ms`],
          [t("Rules"), int(r.rules)],
        ]} />
        <div className="span-2">
          {r.engine === "model" ? (
            <KV items={[
              [t("Train / test"), `${d.train_window ?? "-"} / ${d.test_window ?? "-"}`],
              [t("Features"), d.n_features ?? "-"],
              [t("Decline threshold"), num(d.policy?.decline_threshold, 3)],
              [t("Review capacity"), pct(d.policy?.review_capacity)],
              [t("Review cost (shadow price)"), d.policy ? usd(d.policy.review_cost) : "-"],
              [t("Challenger"), <span className="mono">{d.challenger ?? t("none")}</span>],
            ]} />
          ) : r.enabled ? (
            <div className="stack">
              <KV items={[
                [t("Intercept (log-odds)"), num(d.intercept, 2)],
                [t("Thresholds"), d.thresholds ? t("review ≥ {review}, decline ≥ {decline}", { review: pct(d.thresholds.review, 0), decline: pct(d.thresholds.decline, 0) }) : "-"],
                [t("Travel Rule threshold"), d.travel_rule_threshold_usd !== undefined ? usd(d.travel_rule_threshold_usd) : "-"],
                ...(r.rail === "crypto" ? [
                  [t("OFAC sanctioned addresses"), int(d.sanctioned_addresses)],
                  [t("On-chain intelligence"), <span className="mono">{d.intel_version ?? t("not loaded (sanctions + scorecard only)")}</span>],
                  [t("Counterparty thresholds"), d.counterparty_thresholds ? t("review ≥ {review}, block ≥ {block}", { review: num(d.counterparty_thresholds.review, 3), block: num(d.counterparty_thresholds.block, 3) }) : "-"],
                ] as [string, ReactNode][] : []),
              ]} />
              <p className="small muted" style={{ margin: 0 }}>{t("Thresholds and amounts are in USD; payments in other currencies are converted before scoring.")}</p>
              {r.details.weights && (
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>{t("Condition")}</th><th className="num">{t("Points")}</th><th>{t("Reason")}</th></tr></thead>
                  <tbody>{r.details.weights.map((w, i) => (
                    <tr key={i}><td className="mono small">{w.feature} {w.op} {String(w.value)}</td><td className="num">+{num(w.points, 1)}</td><td className="small">{w.reason}</td></tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          ) : <span className="muted">{t("Not enabled on this deployment ({setting}).", { setting: "PAYGUARD_RAILS_ENABLED" })}</span>}
        </div>
      </div>
    </Card>
  );
}
