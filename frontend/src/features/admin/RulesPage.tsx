import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, RailBadge, Select } from "@/components/ui";
import { int, pct } from "@/lib/format";
import { useUrlState } from "@/lib/useUrlState";
import { RAIL_OPTIONS } from "@/features/options";

export function RulesPage() {
  const { values, set } = useUrlState({ rail: "all", mode: "all" });
  const q = useQuery({ queryKey: ["admin", "rules"], queryFn: admin.rules });
  return (
    <>
      <PageHeader title="Rules"
        description="Declarative rules on every rail. Enforce rules can escalate a decision; shadow rules are evaluated and logged but never act. Rules are demoted to shadow when their measured marginal precision doesn't pay for the reviews they cause." />
      <Async query={q}>
        {(r) => {
          const rows = r.rules.filter((x) => (values.rail === "all" || x.rail === values.rail) && (values.mode === "all" || x.mode === values.mode));
          return (
            <Card flush title={`${rows.length} rules`} subtitle={`Hit counts over the ${int(r.sampled_decisions)} most recent decisions`}
              actions={<div className="row">
                <Select label="Rail" value={values.rail} onChange={(v) => set("rail", v)} options={RAIL_OPTIONS} />
                <Select label="Mode" value={values.mode} onChange={(v) => set("mode", v)}
                  options={[{ value: "all", label: "All" }, { value: "enforce", label: "Enforce" }, { value: "shadow", label: "Shadow" }]} />
              </div>}>
              {rows.length === 0 ? <Empty>No rules match.</Empty> : (
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>Rule</th><th>Rail</th><th>Mode</th><th>Action</th><th>Conditions (all must hold)</th><th className="num">Hits</th><th className="num">Hit rate</th></tr></thead>
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
