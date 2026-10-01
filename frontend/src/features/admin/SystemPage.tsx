import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Card, Health, Json, KV, PageHeader } from "@/components/ui";
import { dateTime, duration, humanize } from "@/lib/format";

export function SystemPage() {
  const q = useQuery({ queryKey: ["admin", "system"], queryFn: admin.system, refetchInterval: 30_000 });
  return (
    <>
      <PageHeader title="System" description="Runtime components and non-secret settings of the API process serving this console." />
      <Async query={q}>
        {(s) => {
          const { health, rails, ...components } = s.components;
          return (
            <div className="grid grid-3">
              <Card title="Process">
                <KV items={[["Version", s.version], ["Started", dateTime(s.started_at)], ["Uptime", duration(s.uptime_s)], ["Rails", rails.join(", ")]]} />
              </Card>
              <Card title="Health">
                <div className="stack" style={{ gap: 8 }}>{Object.entries(health).map(([k, v]) => <Health key={k} ok={v} label={k} />)}</div>
              </Card>
              <Card title="Settings"><Json value={s.settings} /></Card>
              <Card title="Components" className="span-2">
                <KV items={Object.entries(components).map(([k, v]) => [humanize(k), typeof v === "object" ? <span className="mono small">{Object.entries(v as object).map(([a, b]) => `${a}=${String(b)}`).join("  ")}</span> : String(v)])} />
              </Card>
            </div>
          );
        }}
      </Async>
    </>
  );
}
