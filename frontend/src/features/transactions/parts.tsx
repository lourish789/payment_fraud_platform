import type { DecisionOut } from "@/api/types";
import { Badge, Card, Code, Json, KV } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, num, prob, usd } from "@/lib/format";

export function DecisionCard({ d }: { d: DecisionOut }) {
  const t = useT();
  const enforced = d.rules.filter((r) => !r.startsWith("shadow:"));
  const shadow = d.rules.filter((r) => r.startsWith("shadow:")).map((r) => r.slice(7));
  const none = t("none");
  return (
    <Card title={t("Decision")}>
      <KV items={[
        [t("Decision"), <Badge value={d.decision} />],
        [t("Fraud probability"), <b>{prob(d.fraud_probability)}</b>],
        [t("Expected loss"), usd(d.expected_loss)],
        [t("Required actions"), d.actions.length ? d.actions.map((a) => <span key={a} className="chip"><Code value={a} /></span>) : none],
        [t("Reasons"), d.reasons.length ? d.reasons.map((r) => <div key={r.code} className="small"><span className="mono">{r.code}</span> {r.detail}</div>) : "-"],
        [t("Rules (enforced)"), enforced.length ? enforced.map((r) => <span key={r} className="chip mono">{r}</span>) : none],
        [t("Rules (shadow)"), shadow.length ? shadow.map((r) => <span key={r} className="chip mono">{r}</span>) : none],
        [t("Model / scorecard"), <span className="mono">{d.model_version}</span>],
        [t("Challenger (shadow)"), d.shadow ? <span className="mono">{String(d.shadow.version)}: {prob(Number(d.shadow.fraud_probability))}</span> : "-"],
        [t("Latency"), `${num(d.latency_ms, 2)} ms`],
        [t("Decided at"), dateTime(d.created_at)],
      ]} />
    </Card>
  );
}

export function PayloadCard({ payload }: { payload: Record<string, unknown> }) {
  const t = useT();
  return (
    <Card title={t("Payment as submitted")}>
      <Json value={payload} />
    </Card>
  );
}
