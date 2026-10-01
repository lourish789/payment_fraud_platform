import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { investigations } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, Select } from "@/components/ui";
import { dateTime, pct } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 25;

export function InvestigationsPage() {
  const { values, set, offset, setOffset } = useUrlState({ status: "all", recommendation: "all" });
  const q = useQuery({
    queryKey: ["investigations", values, offset],
    queryFn: () => investigations.list({ status: opt(values.status), recommendation: opt(values.recommendation), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  return (
    <>
      <PageHeader title="Investigations" description="Agent reports across all cases. Each one cites the tool calls its evidence came from." />
      <Card flush title={q.data ? `${q.data.total.toLocaleString()} investigations` : "Investigations"}
        actions={<div className="row">
          <Select label="Status" value={values.status} onChange={(v) => set("status", v)}
            options={["all", "queued", "running", "done", "failed"].map((v) => ({ value: v, label: v }))} />
          <Select label="Recommendation" value={values.recommendation} onChange={(v) => set("recommendation", v)}
            options={["all", "fraud", "legit", "escalate"].map((v) => ({ value: v, label: v }))} />
        </div>}>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>No investigations yet.</Empty> : (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>Case</th><th>Status</th><th>Recommendation</th><th className="num">Confidence</th><th>Summary</th>
                  <th>Provider</th><th className="num">Tokens</th><th>Finished</th></tr></thead>
                <tbody>
                  {page.items.map((i) => (
                    <tr key={i.investigation_id}>
                      <td><Link className="mono" to={`/cases/${i.case_id}`}>{i.case_id}</Link></td>
                      <td><Badge value={i.status} /></td>
                      <td>{i.recommendation ? <Badge value={i.recommendation} /> : "-"}</td>
                      <td className="num">{pct(i.confidence, 0)}</td>
                      <td className="small" style={{ maxWidth: 420 }}>{i.report?.summary ?? i.error ?? "-"}</td>
                      <td className="small">{i.provider ?? "-"}</td>
                      <td className="num small">{(i.tokens.input + i.tokens.output).toLocaleString()}</td>
                      <td className="small nowrap">{dateTime(i.finished_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
              <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
