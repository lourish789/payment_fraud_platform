import { useQuery } from "@tanstack/react-query";
import { monitoring } from "@/api/endpoints";
import { HorizontalBars } from "@/components/charts";
import { Async, Badge, Card, Empty, KV, PageHeader, Stat } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, int, num, pct } from "@/lib/format";

export function MonitoringPage() {
  const t = useT();
  const q = useQuery({ queryKey: ["drift"], queryFn: monitoring.drift, refetchInterval: 60_000 });
  return (
    <>
      <PageHeader title={t("Model monitoring")}
        description={t("Population Stability Index of live serving-time features and scores against the model's validation month. Labels arrive weeks late; drift is the early warning.")} />
      <Async query={q}>
        {(d) => d.status === "insufficient_data" ? (
          <Card><Empty>{t("Not enough decisions by the current champion yet ({n} of 500 needed).", { n: int(d.n) })}</Empty></Card>
        ) : (
          <div className="stack">
            <div className="grid grid-4">
              <Stat label={t("Status")} value={<Badge value={d.status} />} sub={t("warn > {warn}, alert > {alert}", { warn: d.thresholds?.warn, alert: d.thresholds?.alert })} />
              <Stat label={t("Score PSI")} value={num(d.score_psi, 4)} sub={t("over {n} decisions", { n: int(d.n) })} />
              <Stat label={t("Flag rate")} value={pct(d.flag_rate, 2)} />
              <Stat label={t("Live precision (flagged)")} value={pct(d.live_precision_flagged)} sub={t("{n} flagged with labels", { n: int(d.flagged_labelled) })} />
            </div>
            <div className="grid grid-3">
              <Card className="span-2" title={t("Feature drift (PSI)")} subtitle={t("Most important features, worst first")}>
                <HorizontalBars data={Object.entries(d.features_psi ?? {}).slice(0, 25).map(([name, value]) => ({ name, value }))}
                                color="review" format={(v) => num(v, 3)} />
              </Card>
              <Card title={t("Details")}>
                <KV items={[
                  [t("Model"), <span className="mono">{d.model_version}</span>],
                  [t("Window"), d.window ? t("{from} to {to}", { from: dateTime(d.window.from), to: dateTime(d.window.to) }) : "-"],
                  [t("Drifted features"), d.drifted_features?.length ? d.drifted_features.map((f) => <span key={f} className="chip mono">{f}</span>) : t("none")],
                ]} />
              </Card>
            </div>
          </div>
        )}
      </Async>
    </>
  );
}
