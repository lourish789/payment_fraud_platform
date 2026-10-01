import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { models } from "@/api/endpoints";
import type { ModelMetadata, Registry } from "@/api/types";
import { Async, Badge, Card, ErrorState, Json, KV, PageHeader } from "@/components/ui";
import { dateTime, num, pct } from "@/lib/format";

export function ModelsPage() {
  const reg = useQuery({ queryKey: ["models"], queryFn: models.registry });
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="Model registry"
        description="Immutable versions; promotion is a pointer swap with who/when/why, hot-swapped into serving without a restart. The challenger scores every card transaction in shadow." />
      <Async query={reg}>
        {(r) => (
          <div className="grid grid-3">
            <div className="stack span-2">
              <Card title="Versions" flush>
                <table className="table">
                  <thead><tr><th>Version</th><th>Alias</th><th /></tr></thead>
                  <tbody>
                    {r.versions.map((v) => (
                      <tr key={v} className="clickable" onClick={() => setSelected(v)}>
                        <td className="mono">{v}</td>
                        <td>{v === r.champion && <Badge value="champion" tone="approve" />}{v === r.challenger && <Badge value="challenger" tone="review" />}</td>
                        <td className="num"><button className="btn small ghost">Details</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
              {(selected ?? r.champion) && <ModelDetail version={(selected ?? r.champion)!} />}
            </div>
            <div className="stack">
              <PromoteCard r={r} />
              <Card title="Promotion history" flush>
                <table className="table"><tbody>
                  {[...r.history].reverse().map((h, i) => (
                    <tr key={i}><td className="small">
                      <b>{h.alias}</b>: <span className="mono">{h.from ?? "none"}</span> → <span className="mono">{h.to ?? "none"}</span>
                      <div className="muted">{h.reason} · {dateTime(h.at)}</div>
                    </td></tr>
                  ))}
                </tbody></table>
              </Card>
            </div>
          </div>
        )}
      </Async>
    </>
  );
}

function ModelDetail({ version }: { version: string }) {
  const q = useQuery({ queryKey: ["model", version], queryFn: () => models.get(version) });
  return (
    <Card title={<span className="mono">{version}</span>} subtitle="Training metadata and offline evaluation (out-of-time test month)">
      <Async query={q}>{(m) => <Metadata m={m} />}</Async>
    </Card>
  );
}

function Metadata({ m }: { m: ModelMetadata }) {
  const report = (m.report ?? {}) as Record<string, any>;
  const test = report.test?.calibrated ?? {};
  const valid = report.valid?.calibrated ?? {};
  const policy = report.policy ?? {};
  return (
    <div className="stack">
      <div className="grid grid-2">
        <KV items={[
          ["Created", dateTime(m.created_at)],
          ["Train window", m.train_window], ["Validation", m.valid_window], ["Test", m.test_window],
          ["Features", m.n_features], ["Trees", m.best_iteration],
        ]} />
        <KV items={[
          ["Test ROC-AUC", num(test.roc_auc, 4)], ["Test PR-AUC", num(test.pr_auc, 4)],
          ["Recall @ 1% FPR", pct(test.recall_at_1pct_fpr)], ["ECE", num(test.ece, 4)],
          ["Validation PR-AUC", num(valid.pr_auc, 4)],
          ["Decline threshold", num(policy.decline_threshold, 3)],
          ["Review cost (shadow price)", policy.review_cost ? `$${num(policy.review_cost, 2)}` : "-"],
        ]} />
      </div>
      <details><summary>Full metadata</summary><Json value={m} /></details>
    </div>
  );
}

function PromoteCard({ r }: { r: Registry }) {
  const qc = useQueryClient();
  const [alias, setAlias] = useState<"champion" | "challenger">("challenger");
  const [version, setVersion] = useState<string>("");
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => models.promote(alias, version || null, reason),
    onSuccess: () => { setReason(""); qc.invalidateQueries({ queryKey: ["models"] }); qc.invalidateQueries({ queryKey: ["readyz"] }); },
  });
  function submit(e: FormEvent) { e.preventDefault(); m.mutate(); }
  return (
    <Card title="Promote" subtitle="Changes serving immediately">
      <form className="stack" onSubmit={submit}>
        <KV items={[["Champion", <span className="mono">{r.champion ?? "none"}</span>], ["Challenger", <span className="mono">{r.challenger ?? "none"}</span>]]} />
        <label className="field"><span>Alias</span>
          <select className="select" value={alias} onChange={(e) => setAlias(e.target.value as typeof alias)}>
            <option value="challenger">challenger (shadow)</option><option value="champion">champion (decides)</option>
          </select></label>
        <label className="field"><span>Version</span>
          <select className="select" value={version} onChange={(e) => setVersion(e.target.value)}>
            <option value="">{alias === "challenger" ? "none (clear challenger)" : "choose a version"}</option>
            {r.versions.map((v) => <option key={v} value={v}>{v}</option>)}
          </select></label>
        <label className="field"><span>Reason (recorded in history and the audit log)</span>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required /></label>
        <button className="btn primary" disabled={m.isPending || reason.length < 3 || (alias === "champion" && !version)}>Promote</button>
        {m.error && <ErrorState error={m.error} />}
        {m.isSuccess && <div className="alert-box success">Registry updated; serving reloaded.</div>}
      </form>
    </Card>
  );
}
