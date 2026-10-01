import type { DecisionOut } from "@/api/types";
import { Badge, Card, Json, KV } from "@/components/ui";
import { dateTime, humanize, money, num, prob } from "@/lib/format";

export function DecisionCard({ d }: { d: DecisionOut }) {
  const enforced = d.rules.filter((r) => !r.startsWith("shadow:"));
  const shadow = d.rules.filter((r) => r.startsWith("shadow:")).map((r) => r.slice(7));
  return (
    <Card title="Decision">
      <KV items={[
        ["Decision", <Badge value={d.decision} />],
        ["Fraud probability", <b>{prob(d.fraud_probability)}</b>],
        ["Expected loss", money(d.expected_loss)],
        ["Required actions", d.actions.length ? d.actions.map((a) => <span key={a} className="chip">{humanize(a)}</span>) : "none"],
        ["Reasons", d.reasons.length ? d.reasons.map((r) => <div key={r.code} className="small"><span className="mono">{r.code}</span> {r.detail}</div>) : "-"],
        ["Rules (enforced)", enforced.length ? enforced.map((r) => <span key={r} className="chip mono">{r}</span>) : "none"],
        ["Rules (shadow)", shadow.length ? shadow.map((r) => <span key={r} className="chip mono">{r}</span>) : "none"],
        ["Model / scorecard", <span className="mono">{d.model_version}</span>],
        ["Challenger (shadow)", d.shadow ? <span className="mono">{String(d.shadow.version)}: {prob(Number(d.shadow.fraud_probability))}</span> : "-"],
        ["Latency", `${num(d.latency_ms, 2)} ms`],
        ["Decided at", dateTime(d.created_at)],
      ]} />
    </Card>
  );
}

export function PayloadCard({ payload }: { payload: Record<string, unknown> }) {
  return (
    <Card title="Payment as submitted">
      <Json value={payload} />
    </Card>
  );
}
