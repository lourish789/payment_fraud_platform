import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { scoring } from "@/api/endpoints";
import { RAILS, type Rail, type ScoreResponse } from "@/api/types";
import { Amount, Badge, Card, Code, ErrorState, KV, PageHeader, Segmented } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";
import { useI18n, useT } from "@/i18n";
import { RAIL_LABEL } from "@/i18n/labels";
import { displayCurrency, money, num, prob, usd } from "@/lib/format";
import { template } from "./templates";

export function ScoringConsolePage() {
  const { t, rates } = useI18n();
  const [rail, setRail] = useState<Rail>("card");
  const [currency, setCurrency] = useState("USD");
  const [text, setText] = useState(() => JSON.stringify(template("card"), null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [history, setHistory] = useState<ScoreResponse[]>([]);
  const m = useMutation({ mutationFn: scoring.score, onSuccess: (r) => setHistory((h) => [r, ...h].slice(0, 10)) });

  function load(r: Rail, cur = currency) {
    setRail(r);
    setCurrency(cur);
    setText(JSON.stringify(template(r, cur, rates.rates[cur] ?? 1), null, 2));
    setParseError(null);
    m.reset();
  }

  function submit() {
    setParseError(null);
    try {
      m.mutate(JSON.parse(text));
    } catch (e) {
      setParseError((e as Error).message);
    }
  }

  return (
    <>
      <PageHeader title={t("Scoring console")}
        description={t("Send a payment to {endpoint} exactly as an integration would. Requests are real: they are stored, and flagged ones open cases.", { endpoint: "POST /v1/payments/score" })} />
      <div className="grid grid-2">
        <Card title={t("Request")} actions={<div className="row">
          <Segmented label={t("Rail")} value={rail} onChange={(r) => load(r)} options={RAILS.map((r) => ({ value: r, label: t(RAIL_LABEL[r]) }))} />
          {rail !== "crypto" && rates.display.length > 1 && (
            <Segmented label={t("Payment currency")} value={currency} onChange={(c) => load(rail, c)}
              options={rates.display.map((c) => ({ value: c, label: c }))} />
          )}
        </div>}>
          <div className="stack">
            <textarea className="textarea" rows={22} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} aria-label={t("Payment JSON")} />
            <div className="row">
              <button className="btn primary" onClick={submit} disabled={m.isPending}>{m.isPending ? t("Scoring...") : t("Score payment")}</button>
              <button className="btn" onClick={() => load(rail)}>{t("New transaction id")}</button>
            </div>
            {rail !== "crypto" && <p className="small muted" style={{ margin: 0 }}>
              {t("Amounts in any supported currency are converted to USD before scoring (models and thresholds are in USD). Crypto must send amount_usd.")}
            </p>}
            {parseError && <div className="alert-box error">{t("Invalid JSON: {error}", { error: parseError })}</div>}
            {m.error && <ErrorState error={m.error} />}
          </div>
        </Card>
        <div className="stack">
          {m.data ? <ResultCard r={m.data} /> : <Card title={t("Response")}><span className="muted">{t("Submit a payment to see the decision.")}</span></Card>}
          {history.length > 1 && (
            <Card title={t("This session")} flush>
              <table className="table"><tbody>
                {history.map((h) => (
                  <tr key={h.transaction_id + h.latency_ms}>
                    <td className="mono small">{h.transaction_id}</td><td><Badge value={h.decision} /></td>
                    <td className="num small">{h.amount !== null ? money(h.amount, h.currency ?? "USD") : "-"}</td>
                    <td className="num">{prob(h.fraud_probability)}</td><td className="num small">{num(h.latency_ms, 1)} ms</td>
                  </tr>
                ))}
              </tbody></table>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function ResultCard({ r }: { r: ScoreResponse }) {
  const t = useT();
  const { can } = useAuth();
  const none = t("none");
  return (
    <Card title={t("Decision")} actions={<Badge value={r.decision} />}>
      <KV items={[
        [t("Fraud probability"), <b>{prob(r.fraud_probability)}</b>],
        [t("Amount"), r.amount !== null ? <Amount amount={r.amount} currency={r.currency} amountUsd={r.amount_usd} /> : "-"],
        [t("Scored as (USD)"), r.amount_usd !== null ? <>{money(r.amount_usd, "USD")}{r.fx_rate && r.fx_rate !== 1
          ? <span className="small muted"> · {t("rate {rate} {currency}/USD", { rate: num(r.fx_rate, 2), currency: r.currency })}</span> : null}</> : "-"],
        [t("Expected loss"), <>{usd(r.expected_loss)}{r.expected_loss_local !== null && r.currency && r.currency !== displayCurrency()
          ? <span className="small muted"> ({money(r.expected_loss_local, r.currency)})</span> : null}</>],
        [t("Required actions"), r.required_actions.length ? r.required_actions.map((a) => <span key={a} className="chip"><Code value={a} /></span>) : none],
        [t("Reasons"), r.reasons.length ? r.reasons.map((x) => <div key={x.code} className="small"><span className="mono">{x.code}</span> {x.detail}</div>) : none],
        [t("Rules triggered"), r.rules_triggered.length ? r.rules_triggered.map((x) => <span key={x} className="chip mono">{x}</span>) : none],
        [t("Model / scorecard"), <span className="mono">{r.model_version}</span>],
        [t("Case"), r.case_id ? (can("cases:read") ? <Link to={`/cases/${r.case_id}`} className="mono">{r.case_id}</Link> : <span className="mono">{r.case_id}</span>) : t("none (approved)")],
        [t("Latency"), `${num(r.latency_ms, 2)} ms`],
        [t("Idempotent replay"), r.idempotent_replay ? t("yes (same id + payload: original decision returned)") : t("no")],
        [t("Degraded mode"), r.degraded ? t("yes: feature store unavailable") : t("no")],
      ]} />
    </Card>
  );
}
