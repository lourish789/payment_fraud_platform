import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { models } from "@/api/endpoints";
import type { ModelMetadata, Registry } from "@/api/types";
import { Async, Badge, Card, Code, ErrorState, Json, KV, PageHeader } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, num, pct, usd } from "@/lib/format";

export function ModelsPage() {
  const t = useT();
  const reg = useQuery({ queryKey: ["models"], queryFn: models.registry });
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <PageHeader title={t("Model registry")}
        description={t("Immutable versions; promotion is a pointer swap with who/when/why, hot-swapped into serving without a restart. The challenger scores every card transaction in shadow.")} />
      <Async query={reg}>
        {(r) => (
          <div className="grid grid-3">
            <div className="stack span-2">
              <Card title={t("Versions")} flush>
                <table className="table">
                  <thead><tr><th>{t("Version")}</th><th>{t("Alias")}</th><th /></tr></thead>
                  <tbody>
                    {r.versions.map((v) => (
                      <tr key={v} className="clickable" onClick={() => setSelected(v)}>
                        <td className="mono">{v}</td>
                        <td>{v === r.champion && <Badge value="champion" tone="approve" />}{v === r.challenger && <Badge value="challenger" tone="review" />}</td>
                        <td className="num"><button className="btn small ghost">{t("Details")}</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
              {(selected ?? r.champion) && <ModelDetail version={(selected ?? r.champion)!} />}
            </div>
            <div className="stack">
              <PromoteCard r={r} />
              <Card title={t("Promotion history")} flush>
                <table className="table"><tbody>
                  {[...r.history].reverse().map((h, i) => (
                    <tr key={i}><td className="small">
                      <b><Code value={h.alias} /></b>: <span className="mono">{h.from ?? t("none")}</span> → <span className="mono">{h.to ?? t("none")}</span>
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
  const t = useT();
  const q = useQuery({ queryKey: ["model", version], queryFn: () => models.get(version) });
  return (
    <Card title={<span className="mono">{version}</span>} subtitle={t("Training metadata and offline evaluation (out-of-time test month)")}>
      <Async query={q}>{(m) => <Metadata m={m} />}</Async>
    </Card>
  );
}

function Metadata({ m }: { m: ModelMetadata }) {
  const t = useT();
  const report = (m.report ?? {}) as Record<string, any>;
  const test = report.test?.calibrated ?? {};
  const valid = report.valid?.calibrated ?? {};
  const policy = report.policy ?? {};
  return (
    <div className="stack">
      <div className="grid grid-2">
        <KV items={[
          [t("Created"), dateTime(m.created_at)],
          [t("Train window"), m.train_window], [t("Validation"), m.valid_window], [t("Test"), m.test_window],
          [t("Features"), m.n_features], [t("Trees"), m.best_iteration],
        ]} />
        <KV items={[
          [t("Test ROC-AUC"), num(test.roc_auc, 4)], [t("Test PR-AUC"), num(test.pr_auc, 4)],
          [t("Recall @ 1% FPR"), pct(test.recall_at_1pct_fpr)], ["ECE", num(test.ece, 4)],
          [t("Validation PR-AUC"), num(valid.pr_auc, 4)],
          [t("Decline threshold"), num(policy.decline_threshold, 3)],
          [t("Review cost (shadow price)"), policy.review_cost ? usd(policy.review_cost) : "-"],
        ]} />
      </div>
      <details><summary>{t("Full metadata")}</summary><Json value={m} /></details>
    </div>
  );
}

function PromoteCard({ r }: { r: Registry }) {
  const t = useT();
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
    <Card title={t("Promote")} subtitle={t("Changes serving immediately")}>
      <form className="stack" onSubmit={submit}>
        <KV items={[[t("Champion"), <span className="mono">{r.champion ?? t("none")}</span>], [t("Challenger"), <span className="mono">{r.challenger ?? t("none")}</span>]]} />
        <label className="field"><span>{t("Alias")}</span>
          <select className="select" value={alias} onChange={(e) => setAlias(e.target.value as typeof alias)}>
            <option value="challenger">{t("challenger (shadow)")}</option><option value="champion">{t("champion (decides)")}</option>
          </select></label>
        <label className="field"><span>{t("Version")}</span>
          <select className="select" value={version} onChange={(e) => setVersion(e.target.value)}>
            <option value="">{alias === "challenger" ? t("none (clear challenger)") : t("choose a version")}</option>
            {r.versions.map((v) => <option key={v} value={v}>{v}</option>)}
          </select></label>
        <label className="field"><span>{t("Reason (recorded in history and the audit log)")}</span>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required /></label>
        <button className="btn primary" disabled={m.isPending || reason.length < 3 || (alias === "champion" && !version)}>{t("Promote")}</button>
        {m.error && <ErrorState error={m.error} />}
        {m.isSuccess && <div className="alert-box success">{t("Registry updated; serving reloaded.")}</div>}
      </form>
    </Card>
  );
}
