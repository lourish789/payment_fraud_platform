import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import type { RailOut } from "@/api/types";
import { Async, Badge, Card, KV, PageHeader, RailBadge } from "@/components/ui";
import { int, money, num, pct } from "@/lib/format";

export function RailsPage() {
  const q = useQuery({ queryKey: ["admin", "rails"], queryFn: admin.rails });
  return (
    <>
      <PageHeader title="Payment rails"
        description="One scoring pipeline, one scorer per rail. Cards use the trained model; bank transfer and mobile money use transparent scorecards (log-odds priors) until labels accumulate; crypto adds sanctions screening and on-chain intelligence." />
      <Async query={q}>{(rails) => <div className="stack">{rails.map((r) => <RailCard key={r.rail} r={r} />)}</div>}</Async>
    </>
  );
}

function RailCard({ r }: { r: RailOut }) {
  const d = r.details as Record<string, any>;
  return (
    <Card title={<span className="row"><RailBadge rail={r.rail} /> <span className="mono small">{r.version ?? "-"}</span></span>}
      actions={<span className="row">{r.enabled ? <Badge value="enabled" tone="approve" /> : <Badge value="disabled" tone="decline" />}<Badge value={r.engine} tone="info" /></span>}>
      <div className="grid grid-3">
        <KV items={[
          ["Transactions (all time)", int(r.stats.total)],
          ["Review / decline", `${int(r.stats.review)} / ${int(r.stats.decline)}`],
          ["Flag rate", pct(r.stats.flag_rate)],
          ["Mean latency", r.stats.mean_latency_ms === null ? "-" : `${num(r.stats.mean_latency_ms, 2)} ms`],
          ["Rules", int(r.rules)],
        ]} />
        <div className="span-2">
          {r.engine === "model" ? (
            <KV items={[
              ["Train / test", `${d.train_window ?? "-"} / ${d.test_window ?? "-"}`],
              ["Features", d.n_features ?? "-"],
              ["Decline threshold", num(d.policy?.decline_threshold, 3)],
              ["Review capacity", pct(d.policy?.review_capacity)],
              ["Review cost (shadow price)", d.policy ? money(d.policy.review_cost) : "-"],
              ["Challenger", <span className="mono">{d.challenger ?? "none"}</span>],
            ]} />
          ) : r.enabled ? (
            <div className="stack">
              <KV items={[
                ["Intercept (log-odds)", num(d.intercept, 2)],
                ["Thresholds", d.thresholds ? `review ≥ ${pct(d.thresholds.review, 0)}, decline ≥ ${pct(d.thresholds.decline, 0)}` : "-"],
                ["Travel Rule threshold", d.travel_rule_threshold_usd !== undefined ? money(d.travel_rule_threshold_usd) : "-"],
                ...(r.rail === "crypto" ? [
                  ["OFAC sanctioned addresses", int(d.sanctioned_addresses)] as [string, string],
                  ["On-chain intelligence", <span className="mono">{d.intel_version ?? "not loaded (sanctions + scorecard only)"}</span>] as [string, JSX.Element],
                  ["Counterparty thresholds", d.counterparty_thresholds ? `review ≥ ${num(d.counterparty_thresholds.review, 3)}, block ≥ ${num(d.counterparty_thresholds.block, 3)}` : "-"] as [string, string],
                ] : []),
              ]} />
              {r.details.weights && (
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>Condition</th><th className="num">Points</th><th>Reason</th></tr></thead>
                  <tbody>{r.details.weights.map((w, i) => (
                    <tr key={i}><td className="mono small">{w.feature} {w.op} {String(w.value)}</td><td className="num">+{num(w.points, 1)}</td><td className="small">{w.reason}</td></tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          ) : <span className="muted">Not enabled on this deployment (PAYGUARD_RAILS_ENABLED).</span>}
        </div>
      </div>
    </Card>
  );
}
