import { useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Card, Health, Json, KV, PageHeader } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, duration, humanize } from "@/lib/format";

export function SystemPage() {
  const t = useT();
  const q = useQuery({ queryKey: ["admin", "system"], queryFn: admin.system, refetchInterval: 30_000 });
  return (
    <>
      <PageHeader title={t("System")} description={t("Runtime components and non-secret settings of the API process serving this console.")} />
      <Async query={q}>
        {(s) => {
          const { health, rails, ...components } = s.components;
          return (
            <div className="grid grid-3">
              <Card title={t("Process")}>
                <KV items={[[t("Version"), s.version], [t("Started"), dateTime(s.started_at)], [t("Uptime"), duration(s.uptime_s)], [t("Rails"), rails.join(", ")]]} />
              </Card>
              <Card title={t("Health")}>
                <div className="stack" style={{ gap: 8 }}>{Object.entries(health).map(([k, v]) => <Health key={k} ok={v} label={k} />)}</div>
              </Card>
              <Card title={t("Settings")}><Json value={s.settings} /></Card>
              <Card title={t("Components")} className="span-2">
                <KV items={Object.entries(components).map(([k, v]) => [humanize(k), typeof v === "object" ? <span className="mono small">{Object.entries(v as object).map(([a, b]) => `${a}=${String(b)}`).join("  ")}</span> : String(v)])} />
              </Card>
            </div>
          );
        }}
      </Async>
    </>
  );
}
