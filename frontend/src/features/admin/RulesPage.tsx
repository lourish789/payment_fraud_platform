import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, RailBadge, Select } from "@/components/ui";
import { useT } from "@/i18n";
import { int, pct } from "@/lib/format";
import { useUrlState } from "@/lib/useUrlState";
import { codeOptions, railOptions } from "@/features/options";

export function RulesPage() {
  const t = useT();
  const { values, set } = useUrlState({ rail: "all", mode: "all" });
  const q = useQuery({ queryKey: ["admin", "rules"], queryFn: admin.rules });
  return (
    <>
      <PageHeader title={t("Rules")}
        description={t("Declarative rules on every rail. Enforce rules can escalate a decision; shadow rules are evaluated and logged but never act. Rules are demoted to shadow when their measured marginal precision doesn't pay for the reviews they cause.")} />
      <Async query={q}>
        {(r) => {
          const rows = r.rules.filter((x) => (values.rail === "all" || x.rail === values.rail) && (values.mode === "all" || x.mode === values.mode));
          return (
            <Card flush title={t("Rules: {n}", { n: rows.length })} subtitle={t("Hit counts over the {n} most recent decisions", { n: int(r.sampled_decisions) })}
              actions={<div className="row">
                <Select label={t("Rail")} value={values.rail} onChange={(v) => set("rail", v)} options={railOptions(t)} />
                <Select label={t("Mode")} value={values.mode} onChange={(v) => set("mode", v)} options={codeOptions(t, ["enforce", "shadow"])} />
              </div>}>
              {rows.length === 0 ? <Empty>{t("No rules match.")}</Empty> : (
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>{t("Rule")}</th><th>{t("Rail")}</th><th>{t("Mode")}</th><th>{t("Action")}</th><th>{t("Conditions (all must hold)")}</th><th className="num">{t("Hits")}</th><th className="num">{t("Hit rate")}</th></tr></thead>
                  <tbody>{rows.map((x) => (
                    <tr key={`${x.rail}:${x.id}`}>
                      <td><span className="mono">{x.id}</span><div className="small muted">{x.description}</div></td>
                      <td><RailBadge rail={x.rail} /></td>
                      <td><Badge value={x.mode} /></td>
                      <td><Badge value={x.action} /></td>
                      <td>{x.conditions.map((c, i) => <div key={i} className="mono small">{c.feature} {c.op} {JSON.stringify(c.value)}</div>)}</td>
                      <td className="num">{int(x.hits)}</td>
                      <td className="num">{r.sampled_decisions ? pct(x.hits / r.sampled_decisions, 2) : "-"}</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </Card>
          );
        }}
      </Async>
    </>
  );
}
