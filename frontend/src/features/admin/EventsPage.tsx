import { Fragment, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, Json, PageHeader, Pagination, Select } from "@/components/ui";
import { dateTime, duration, int } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 25;

export function EventsPage() {
  const { values, set, offset, setOffset } = useUrlState({ topic: "all", pending: "all" });
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["admin", "events", values, offset],
    queryFn: () => admin.events({ topic: opt(values.topic), pending: values.pending === "all" ? undefined : values.pending === "true", limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  return (
    <>
      <PageHeader title="Event pipeline"
        description="Decisions, cases and labels are announced through a transactional outbox: written in the same DB transaction as the change, then relayed to the bus (Redis Streams) for the agent worker and downstream sinks." />
      <Async query={q}>
        {(r) => (
          <div className="stack">
            <Card flush title="Topics">
              <table className="table">
                <thead><tr><th>Topic</th><th className="num">Events</th><th className="num">Pending relay</th><th>Oldest pending</th><th>Last event</th></tr></thead>
                <tbody>{r.topics.map((t) => (
                  <tr key={t.topic}>
                    <td className="mono">{t.topic}</td><td className="num">{int(t.total)}</td>
                    <td className="num">{t.pending ? <Badge value={int(t.pending)} tone="review" /> : <Badge value="0" tone="approve" />}</td>
                    <td>{duration(t.oldest_pending_age_s)}</td><td className="small">{dateTime(t.last_event_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </Card>
            <Card flush title="Recent events" actions={<div className="row">
              <Select label="Topic" value={values.topic} onChange={(v) => set("topic", v)}
                options={["all", "decision.made", "case.created", "label.recorded", "investigation.requested"].map((v) => ({ value: v, label: v }))} />
              <Select label="Relay" value={values.pending} onChange={(v) => set("pending", v)}
                options={[{ value: "all", label: "All" }, { value: "true", label: "Pending" }, { value: "false", label: "Published" }]} />
            </div>}>
              {r.recent.items.length === 0 ? <Empty>No events.</Empty> : (
                <>
                  <div className="table-wrap"><table className="table">
                    <thead><tr><th className="num">#</th><th>Topic</th><th>Key</th><th>Written</th><th>Published</th></tr></thead>
                    <tbody>{r.recent.items.map((e) => (
                      <Fragment key={e.id}>
                        <tr className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                          <td className="num mono">{e.id}</td><td className="mono">{e.topic}</td><td className="mono small">{e.key}</td>
                          <td className="small">{dateTime(e.created_at)}</td>
                          <td className="small">{e.published_at ? dateTime(e.published_at) : <Badge value="pending" tone="review" />}</td>
                        </tr>
                        {open === e.id && <tr><td colSpan={5}><Json value={e.payload} /></td></tr>}
                      </Fragment>
                    ))}</tbody>
                  </table></div>
                  <Pagination total={r.recent.total} limit={LIMIT} offset={offset} onChange={setOffset} />
                </>
              )}
            </Card>
          </div>
        )}
      </Async>
    </>
  );
}
