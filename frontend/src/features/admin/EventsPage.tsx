import { Fragment, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, Json, PageHeader, Pagination, Select } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, duration, int } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 25;

export function EventsPage() {
  const t = useT();
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
      <PageHeader title={t("Event pipeline")}
        description={t("Decisions, cases and labels are announced through a transactional outbox: written in the same DB transaction as the change, then relayed to the bus (Redis Streams) for the agent worker and downstream sinks.")} />
      <Async query={q}>
        {(r) => (
          <div className="stack">
            <Card flush title={t("Topics")}>
              <table className="table">
                <thead><tr><th>{t("Topic")}</th><th className="num">{t("Events")}</th><th className="num">{t("Pending relay")}</th><th>{t("Oldest pending")}</th><th>{t("Last event")}</th></tr></thead>
                <tbody>{r.topics.map((x) => (
                  <tr key={x.topic}>
                    <td className="mono">{x.topic}</td><td className="num">{int(x.total)}</td>
                    <td className="num">{x.pending ? <Badge value={int(x.pending)} tone="review" /> : <Badge value="0" tone="approve" />}</td>
                    <td>{duration(x.oldest_pending_age_s)}</td><td className="small">{dateTime(x.last_event_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </Card>
            <Card flush title={t("Recent events")} actions={<div className="row">
              <Select label={t("Topic")} value={values.topic} onChange={(v) => set("topic", v)}
                options={[{ value: "all", label: t("All") }, ...["decision.made", "case.created", "label.recorded", "investigation.requested"].map((v) => ({ value: v, label: v }))]} />
              <Select label={t("Relay")} value={values.pending} onChange={(v) => set("pending", v)}
                options={[{ value: "all", label: t("All") }, { value: "true", label: t("Pending") }, { value: "false", label: t("Published") }]} />
            </div>}>
              {r.recent.items.length === 0 ? <Empty>{t("No events.")}</Empty> : (
                <>
                  <div className="table-wrap"><table className="table">
                    <thead><tr><th className="num">#</th><th>{t("Topic")}</th><th>{t("Key")}</th><th>{t("Written")}</th><th>{t("Published")}</th></tr></thead>
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
