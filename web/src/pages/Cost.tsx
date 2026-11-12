import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { useFetch } from "../hooks";
import { Card, Empty, ErrorNote, PageHeader, Spinner, Stat } from "../components/ui";

// USD per 1M tokens: [input, cached input, output]. Cache-write tokens are not tracked
// separately, so days that rewrite the prefix cost slightly more than shown.
const PRICES: Record<string, [number, number, number]> = {
  "gpt-6-luna": [0.1, 0.01, 0.5],
  "gpt-6.1-terra": [1.0, 0.05, 5.0],
};

function costUsd(model: string, input: number, cached: number, output: number): number {
  const price = PRICES[model];
  if (!price) return 0;
  const fresh = Math.max(input - cached, 0);
  return (fresh * price[0] + cached * price[1] + output * price[2]) / 1_000_000;
}

const AXES = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a1a1aa" },
} as const;

export default function Cost({ userId }: { userId: string }) {
  const usage = useFetch(() => api.usage(userId, 30), [userId]);

  if (usage.loading) return <Spinner />;
  if (usage.error) return <ErrorNote error={usage.error} />;
  if (!usage.data || usage.data.by_model_purpose.length === 0) {
    return (
      <div>
        <PageHeader title="Cost & caching" />
        <Card>
          <Empty>No LLM calls recorded yet. Ask the coach something first.</Empty>
        </Card>
      </div>
    );
  }

  const rows = usage.data.by_model_purpose;
  const totalIn = rows.reduce((s, r) => s + r.input_tokens, 0);
  const totalCached = rows.reduce((s, r) => s + r.cached_tokens, 0);
  const totalOut = rows.reduce((s, r) => s + r.output_tokens, 0);
  const totalCalls = rows.reduce((s, r) => s + r.calls, 0);
  const totalCost = rows.reduce((s, r) => s + costUsd(r.model, r.input_tokens, r.cached_tokens, r.output_tokens), 0);
  const hitRate = totalIn ? Math.round((totalCached / totalIn) * 100) : 0;

  return (
    <div>
      <PageHeader title="Cost & caching" subtitle={`Last ${usage.data.days} days of LLM usage. The stable prompt prefix is cached for 12h; sessions for 30m.`} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Calls" value={String(totalCalls)} />
        <Stat label="Tokens in / out" value={`${(totalIn / 1000).toFixed(1)}k / ${(totalOut / 1000).toFixed(1)}k`} />
        <Stat label="Cache hit rate" value={`${hitRate}%`} delta={hitRate >= 50 ? "healthy" : "warming up"} deltaGood={hitRate >= 50} />
        <Stat label="Estimated cost" value={`$${totalCost.toFixed(3)}`} delta={`~$${(totalCost * 30).toFixed(2)}/yr at this pace`} />
      </div>

      <Card title="By model and purpose" className="mt-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wider text-zinc-500">
                <th className="py-2 pr-3">Model</th>
                <th className="py-2 pr-3">Purpose</th>
                <th className="py-2 pr-3 text-right">Calls</th>
                <th className="py-2 pr-3 text-right">Input</th>
                <th className="py-2 pr-3 text-right">Cached</th>
                <th className="py-2 pr-3 text-right">Output</th>
                <th className="py-2 text-right">Est. cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {rows.map((r) => (
                <tr key={`${r.model}-${r.purpose}`} className="text-zinc-300">
                  <td className="py-2 pr-3 font-mono text-xs">{r.model}</td>
                  <td className="py-2 pr-3">{r.purpose}</td>
                  <td className="py-2 pr-3 text-right">{r.calls}</td>
                  <td className="py-2 pr-3 text-right">{r.input_tokens.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-right text-lime-400">{r.cached_tokens.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-right">{r.output_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right font-medium">
                    ${costUsd(r.model, r.input_tokens, r.cached_tokens, r.output_tokens).toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {usage.data.daily.length > 0 && (
        <Card title="Tokens per day (fresh vs cached)" className="mt-4">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart
              data={usage.data.daily.map((d) => ({
                day: d.day.slice(5),
                cached: d.cached_tokens,
                fresh: Math.max(d.input_tokens - d.cached_tokens, 0),
              }))}
            >
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" {...AXES} />
              <YAxis {...AXES} width={50} />
              <Tooltip {...TOOLTIP} cursor={{ fill: "#27272a55" }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="cached" stackId="t" fill="#a3e635" radius={[0, 0, 0, 0]} />
              <Bar dataKey="fresh" stackId="t" fill="#52525b" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  );
}
