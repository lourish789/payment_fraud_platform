// Chart wrappers over Recharts. Colours come from the CSS tokens so charts follow light/dark mode.
import { useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { compactNum, dateShort, moneyCompact, RAIL_LABEL } from "@/lib/format";

const TOKENS = ["approve", "review", "decline", "primary", "text-3", "border", "surface", "text",
  "rail-card", "rail-bank_transfer", "rail-mobile_money", "rail-crypto"] as const;
type Token = (typeof TOKENS)[number];

function readTokens(): Record<Token, string> {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, css.getPropertyValue(`--${t}`).trim()])) as Record<Token, string>;
}

export function useTokens() {
  const [tokens, setTokens] = useState(readTokens);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTokens(readTokens());
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return tokens;
}

const axis = (c: string) => ({ stroke: c, fontSize: 11, tickLine: false, axisLine: false });

function tooltipStyle(t: Record<Token, string>) {
  return { contentStyle: { background: t.surface, border: `1px solid ${t.border}`, borderRadius: 8, color: t.text, fontSize: 12 } };
}

export function DecisionsOverTime({ data, height = 260 }: { data: { t: string; approve: number; review: number; decline: number }[]; height?: number }) {
  const t = useTokens();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
        <CartesianGrid stroke={t.border} vertical={false} />
        <XAxis dataKey="t" tickFormatter={dateShort} {...axis(t["text-3"])} minTickGap={24} />
        <YAxis tickFormatter={compactNum} {...axis(t["text-3"])} />
        <Tooltip labelFormatter={(l) => dateShort(String(l))} {...tooltipStyle(t)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area type="monotone" dataKey="approve" stackId="1" stroke={t.approve} fill={t.approve} fillOpacity={0.25} />
        <Area type="monotone" dataKey="review" stackId="1" stroke={t.review} fill={t.review} fillOpacity={0.45} />
        <Area type="monotone" dataKey="decline" stackId="1" stroke={t.decline} fill={t.decline} fillOpacity={0.55} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function FlaggedRate({ data, height = 260 }: { data: { t: string; total: number; review: number; decline: number }[]; height?: number }) {
  const t = useTokens();
  const rows = data.map((d) => ({ t: d.t, rate: d.total ? (d.review + d.decline) / d.total : 0 }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
        <CartesianGrid stroke={t.border} vertical={false} />
        <XAxis dataKey="t" tickFormatter={dateShort} {...axis(t["text-3"])} minTickGap={24} />
        <YAxis tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} {...axis(t["text-3"])} />
        <Tooltip labelFormatter={(l) => dateShort(String(l))} formatter={(v: number) => `${(v * 100).toFixed(2)}%`} {...tooltipStyle(t)} />
        <Line type="monotone" dataKey="rate" name="flag rate" stroke={t.primary} dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function AmountOverTime({ data, height = 260 }: { data: { t: string; amount: number; flagged_amount: number }[]; height?: number }) {
  const t = useTokens();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={t.border} vertical={false} />
        <XAxis dataKey="t" tickFormatter={dateShort} {...axis(t["text-3"])} minTickGap={24} />
        <YAxis tickFormatter={moneyCompact} {...axis(t["text-3"])} />
        <Tooltip labelFormatter={(l) => dateShort(String(l))} formatter={(v: number) => moneyCompact(v)} {...tooltipStyle(t)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="amount" name="volume" fill={t.primary} fillOpacity={0.35} />
        <Bar dataKey="flagged_amount" name="flagged" fill={t.decline} fillOpacity={0.7} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DecisionDonut({ data, height = 200 }: { data: Record<string, number>; height?: number }) {
  const t = useTokens();
  const rows = (["approve", "review", "decline"] as const).map((k) => ({ name: k, value: data[k] ?? 0 }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie isAnimationActive={false} data={rows} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="85%" paddingAngle={1} stroke="none">
          {rows.map((r) => <Cell key={r.name} fill={t[r.name]} />)}
        </Pie>
        <Tooltip formatter={(v: number) => v.toLocaleString()} {...tooltipStyle(t)} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function HorizontalBars({ data, height, color, format = (v: number) => v.toFixed(3) }: {
  data: { name: string; value: number }[]; height?: number; color?: Token; format?: (v: number) => string;
}) {
  const t = useTokens();
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(120, data.length * 28 + 20)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
        <CartesianGrid stroke={t.border} horizontal={false} />
        <XAxis type="number" tickFormatter={format} {...axis(t["text-3"])} />
        <YAxis type="category" dataKey="name" width={190} {...axis(t["text-3"])} />
        <Tooltip formatter={(v: number) => format(v)} {...tooltipStyle(t)} />
        <Bar dataKey="value" fill={t[color ?? "primary"]} radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function RailVolume({ data, height = 240 }: { data: { rail: string; approve: number; review: number; decline: number }[]; height?: number }) {
  const t = useTokens();
  const rows = data.map((d) => ({ ...d, name: RAIL_LABEL[d.rail] ?? d.rail }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
        <CartesianGrid stroke={t.border} vertical={false} />
        <XAxis dataKey="name" {...axis(t["text-3"])} />
        <YAxis tickFormatter={compactNum} {...axis(t["text-3"])} />
        <Tooltip formatter={(v: number) => v.toLocaleString()} {...tooltipStyle(t)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="approve" stackId="a" fill={t.approve} fillOpacity={0.6} />
        <Bar dataKey="review" stackId="a" fill={t.review} />
        <Bar dataKey="decline" stackId="a" fill={t.decline} />
      </BarChart>
    </ResponsiveContainer>
  );
}
