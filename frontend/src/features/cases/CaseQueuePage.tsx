import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { cases } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, RailBadge, Select } from "@/components/ui";
import { dateTime, money, pct } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { RAIL_OPTIONS } from "@/features/options";

const LIMIT = 25;

export function CaseQueuePage() {
  const navigate = useNavigate();
  const { values, set, offset, setOffset } = useUrlState({ status: "open", rail: "all", decision: "all" });
  const q = useQuery({
    queryKey: ["cases", values, offset],
    queryFn: () => cases.list({ status: values.status, rail: opt(values.rail), decision: opt(values.decision), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader title="Case queue"
        description="Every review or decline on every rail, ordered by expected loss (probability × amount): the most money at risk first." />
      <Card flush title={q.data ? `${q.data.total.toLocaleString()} cases` : "Cases"}
        actions={
          <div className="row">
            <Select label="Status" value={values.status} onChange={(v) => set("status", v)}
              options={[{ value: "open", label: "Open" }, { value: "resolved", label: "Resolved" }, { value: "all", label: "All" }]} />
            <Select label="Rail" value={values.rail} onChange={(v) => set("rail", v)} options={RAIL_OPTIONS} />
            <Select label="Decision" value={values.decision} onChange={(v) => set("decision", v)}
              options={[{ value: "all", label: "All" }, { value: "review", label: "Review" }, { value: "decline", label: "Decline" }]} />
          </div>
        }>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>No cases match these filters.</Empty> : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr>
                    <th>Case</th><th>Rail</th><th>Decision</th><th className="num">Expected loss</th><th className="num">Amount</th>
                    <th>Agent</th><th>Status</th><th>Created</th>
                  </tr></thead>
                  <tbody>
                    {page.items.map((c) => (
                      <tr key={c.case_id} className="clickable" onClick={() => navigate(`/cases/${c.case_id}`)}>
                        <td><span className="mono">{c.case_id}</span><div className="small muted mono">{c.transaction_id}</div></td>
                        <td><RailBadge rail={c.rail} /></td>
                        <td><Badge value={c.decision} /></td>
                        <td className="num"><b>{money(c.priority)}</b></td>
                        <td className="num">{money(c.amount)}</td>
                        <td>{c.agent ? (
                          c.agent.status === "done"
                            ? <><Badge value={c.agent.recommendation ?? "-"} /> <span className="small muted">{pct(c.agent.confidence, 0)}</span></>
                            : <Badge value={c.agent.status} />
                        ) : <span className="muted small">not run</span>}</td>
                        <td>{c.resolution ? <Badge value={c.resolution} /> : <Badge value={c.status} tone="info" />}</td>
                        <td className="nowrap small">{dateTime(c.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
