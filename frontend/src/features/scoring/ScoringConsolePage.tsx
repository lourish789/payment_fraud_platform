import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { scoring } from "@/api/endpoints";
import { RAILS, type Rail, type ScoreResponse } from "@/api/types";
import { Badge, Card, ErrorState, KV, PageHeader, Segmented } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";
import { humanize, money, num, prob, RAIL_LABEL } from "@/lib/format";
import { template } from "./templates";

export function ScoringConsolePage() {
  const [rail, setRail] = useState<Rail>("card");
  const [text, setText] = useState(() => JSON.stringify(template("card"), null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [history, setHistory] = useState<ScoreResponse[]>([]);
  const m = useMutation({ mutationFn: scoring.score, onSuccess: (r) => setHistory((h) => [r, ...h].slice(0, 10)) });

  function load(r: Rail) {
    setRail(r);
    setText(JSON.stringify(template(r), null, 2));
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
      <PageHeader title="Scoring console"
        description={<>Send a payment to <code>POST /v1/payments/score</code> exactly as an integration would. Requests are real: they are stored, and flagged ones open cases.</>} />
      <div className="grid grid-2">
        <Card title="Request" actions={<Segmented label="Rail" value={rail} onChange={load} options={RAILS.map((r) => ({ value: r, label: RAIL_LABEL[r] }))} />}>
          <div className="stack">
            <textarea className="textarea" rows={22} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} aria-label="Payment JSON" />
            <div className="row">
              <button className="btn primary" onClick={submit} disabled={m.isPending}>{m.isPending ? "Scoring..." : "Score payment"}</button>
              <button className="btn" onClick={() => load(rail)}>New transaction id</button>
            </div>
            {parseError && <div className="alert-box error">Invalid JSON: {parseError}</div>}
            {m.error && <ErrorState error={m.error} />}
          </div>
        </Card>
        <div className="stack">
          {m.data ? <ResultCard r={m.data} /> : <Card title="Response"><span className="muted">Submit a payment to see the decision.</span></Card>}
          {history.length > 1 && (
            <Card title="This session" flush>
              <table className="table"><tbody>
                {history.map((h) => (
                  <tr key={h.transaction_id + h.latency_ms}>
                    <td className="mono small">{h.transaction_id}</td><td><Badge value={h.decision} /></td>
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
  const { can } = useAuth();
  return (
    <Card title="Decision" actions={<Badge value={r.decision} />}>
      <KV items={[
        ["Fraud probability", <b>{prob(r.fraud_probability)}</b>],
        ["Expected loss", money(r.expected_loss)],
        ["Required actions", r.required_actions.length ? r.required_actions.map((a) => <span key={a} className="chip">{humanize(a)}</span>) : "none"],
        ["Reasons", r.reasons.map((x) => <div key={x.code} className="small"><span className="mono">{x.code}</span> {x.detail}</div>)],
        ["Rules triggered", r.rules_triggered.length ? r.rules_triggered.map((x) => <span key={x} className="chip mono">{x}</span>) : "none"],
        ["Model / scorecard", <span className="mono">{r.model_version}</span>],
        ["Case", r.case_id ? (can("cases:read") ? <Link to={`/cases/${r.case_id}`} className="mono">{r.case_id}</Link> : <span className="mono">{r.case_id}</span>) : "none (approved)"],
        ["Latency", `${num(r.latency_ms, 2)} ms`],
        ["Idempotent replay", r.idempotent_replay ? "yes (same id + payload: original decision returned)" : "no"],
        ["Degraded mode", r.degraded ? "yes: feature store unavailable" : "no"],
      ]} />
    </Card>
  );
}
