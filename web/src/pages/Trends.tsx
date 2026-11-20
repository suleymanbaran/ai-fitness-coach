import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { useFetch } from "../hooks";
import { Card, Empty, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { adjustedTotalBurn, TOTAL_BURN_FACTOR } from "../calories";

const AXES = { stroke: "#6b7580", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#161c24", border: "1px solid #2c3540", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a7b1bd" },
} as const;

export default function Trends({ userId }: { userId: string }) {
  const [days, setDays] = useState(30);
  const metrics = useFetch(() => api.days(userId, days), [userId, days]);
  const measures = useFetch(() => api.measurements(userId, days), [userId, days]);
  const intake = useFetch(() => api.intake(userId, days), [userId, days]);
  const workouts = useFetch(() => api.workouts(userId, days), [userId, days]);
  // The weekly energy-balance card always looks at a full year, independent of the selector.
  const yearIntake = useFetch(() => api.intake(userId, 365), [userId]);
  const yearMetrics = useFetch(() => api.days(userId, 365), [userId]);
  const profile = useFetch(() => api.user(userId), [userId]);

  // Mifflin-St Jeor BMR (male): 10*kg + 6.25*cm - 5*age + 5. Without age/weight/height the
  // balance falls back to a flat 2,600 kcal/day total burn (user-chosen last resort).
  const bmr = useMemo(() => {
    const p = profile.data;
    if (p?.age != null && p?.weight_kg != null && p?.height_cm != null) {
      return Math.round(10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age + 5);
    }
    return null;
  }, [profile.data]);

  const metricRows = useMemo(
    () =>
      (metrics.data ?? []).map((d) => ({
        date: d.date.slice(5),
        HRV: d.hrv_ms,
        "Resting HR": d.resting_hr_bpm,
        "Sleep (h)": d.sleep_minutes != null ? +(d.sleep_minutes / 60).toFixed(1) : null,
        "Sleep score": d.sleep_score,
        Steps: d.steps,
        "Burned kcal": d.active_kcal,
      })),
    [metrics.data]
  );

  const weightRows = useMemo(
    () =>
      (measures.data ?? [])
        .filter((m) => m.weight_kg != null || m.body_fat_pct != null)
        .map((m) => ({ date: m.date.slice(5), Weight: m.weight_kg, "Body fat %": m.body_fat_pct })),
    [measures.data]
  );

  const intakeRows = useMemo(() => (intake.data ?? []).map((d) => ({ date: d.date.slice(5), kcal: d.kcal })), [intake.data]);

  const workoutRows = useMemo(() => {
    const byWeek = new Map<string, number>();
    for (const w of workouts.data ?? []) {
      const weekStart = new Date(w.date);
      weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7)); // Monday
      const key = weekStart.toISOString().slice(5, 10);
      byWeek.set(key, (byWeek.get(key) ?? 0) + 1);
    }
    return [...byWeek.entries()].sort().map(([date, count]) => ({ date, sessions: count }));
  }, [workouts.data]);

  const [balanceWindow, setBalanceWindow] = useState(7);

  const dailyBalance = useMemo(() => {
    const intakeByDate = new Map((yearIntake.data ?? []).map((d) => [d.date, d.kcal]));
    const metricsByDate = new Map((yearMetrics.data ?? []).map((m) => [m.date, m]));
    const rows: {
      date: string;
      Intake: number | null;
      "Total burn": number | null;
      "Active burn": number | null;
      Balance: number | null;
    }[] = [];
    const today = new Date();
    for (let i = balanceWindow - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const m = metricsByDate.get(key);
      const intake = intakeByDate.get(key) ?? null;
      // Real Fitbit total (×0.8 margin) wins; else BMR estimate + active burn.
      const total =
        m?.total_kcal != null
          ? adjustedTotalBurn(m.total_kcal)
          : bmr != null && m?.active_kcal != null
            ? bmr + m.active_kcal
            : null;
      rows.push({
        date: key.slice(5),
        Intake: intake,
        "Total burn": total,
        "Active burn": m?.active_kcal ?? null,
        Balance: intake != null && total != null ? intake - total : null,
      });
    }
    // Stats over days where BOTH intake and burn exist, so partial logging can't fake a deficit.
    const paired = rows.filter((r) => r.Intake != null && r["Total burn"] != null);
    const totalIntake = paired.reduce((s, r) => s + (r.Intake as number), 0);
    const totalBurn = paired.reduce((s, r) => s + (r["Total burn"] as number), 0);
    return {
      rows,
      days: paired.length,
      avgIntake: paired.length ? Math.round(totalIntake / paired.length) : null,
      avgBurn: paired.length ? Math.round(totalBurn / paired.length) : null,
      avgBalance: paired.length ? Math.round((totalIntake - totalBurn) / paired.length) : null,
      totalBalance: paired.length ? totalIntake - totalBurn : null,
    };
  }, [yearIntake.data, yearMetrics.data, bmr, balanceWindow]);

  if (metrics.loading) return <Spinner />;
  if (metrics.error) return <ErrorNote error={metrics.error} />;

  return (
    <div>
      <PageHeader title="Trends" subtitle="Everything the coach sees, over time.">
        <div className="flex gap-1 rounded-lg border border-edge bg-night-2 p-1">
          {[14, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1 text-xs font-medium ${
                days === d ? "bg-grad-primary font-bold text-night" : "text-mist hover:text-cloud"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Energy balance"
          className="lg:col-span-2"
          action={
            <div className="flex gap-1 rounded-lg border border-edge bg-night p-1">
              {[7, 14, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setBalanceWindow(d)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                    balanceWindow === d ? "bg-grad-primary font-bold text-night" : "text-mist hover:text-cloud"
                  }`}
                >
                  {d}d
                </button>
              ))}
            </div>
          }
        >
          <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-sm">
            <span className="text-mist">
              Avg burn{" "}
              <span className="font-semibold text-ember">{dailyBalance.avgBurn?.toLocaleString() ?? "—"}</span> kcal/day
            </span>
            <span className="text-mist">
              Avg intake{" "}
              <span className="font-semibold text-cloud">{dailyBalance.avgIntake?.toLocaleString() ?? "—"}</span> kcal/day
            </span>
            {dailyBalance.avgBalance != null && (
              <span className={dailyBalance.avgBalance > 0 ? "font-semibold text-magenta" : "font-semibold text-lime"}>
                {dailyBalance.avgBalance > 0
                  ? `+${dailyBalance.avgBalance.toLocaleString()}`
                  : `${Math.abs(dailyBalance.avgBalance).toLocaleString()}`}{" "}
                kcal/day {dailyBalance.avgBalance > 0 ? "surplus" : "deficit"}
              </span>
            )}
            {dailyBalance.totalBalance != null && (
              <span className="text-mist">
                total over {dailyBalance.days} logged day{dailyBalance.days === 1 ? "" : "s"}:{" "}
                <span
                  className={`font-semibold ${dailyBalance.totalBalance > 0 ? "text-magenta" : "text-lime"}`}
                >
                  {dailyBalance.totalBalance > 0 ? "+" : "−"}
                  {Math.abs(dailyBalance.totalBalance).toLocaleString()} kcal
                </span>
              </span>
            )}
          </div>
          {dailyBalance.rows.some((r) => r.Intake != null) ? (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={dailyBalance.rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis {...AXES} width={56} />
                <Tooltip {...TOOLTIP} itemStyle={{ color: "#eef2f6" }} cursor={{ fill: "rgba(163,230,53,0.05)" }} />
                <Legend
                  wrapperStyle={{ fontSize: 12 }}
                  formatter={(value) => <span style={{ color: "#ffffff" }}>{String(value)}</span>}
                />
                <ReferenceLine y={0} stroke="#6b7580" />
                <Bar dataKey="Balance" fill="#a3e635" radius={[4, 4, 0, 0]}>
                  {dailyBalance.rows.map((row, i) => (
                    <Cell key={i} fill={(row.Balance ?? 0) > 0 ? "#fb7185" : "#a3e635"} />
                  ))}
                </Bar>
                <Line dataKey="Intake" stroke="#eef2f6" strokeWidth={2} dot={{ r: 2.5 }} connectNulls />
                <Line dataKey="Total burn" stroke="#fb923c" strokeWidth={1.5} dot={{ r: 2 }} connectNulls />
                <Line dataKey="Active burn" stroke="#a78bfa" strokeWidth={1.5} strokeDasharray="4 4" dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <Empty>No intake logged in this window — log meals to see your daily balance.</Empty>
          )}
          <p className="mt-2 text-xs text-ash">
            Per day: white = intake, orange = total burn (Fitbit total-calories × {TOTAL_BURN_FACTOR} accuracy margin
            {bmr != null ? "; days without Fitbit data use BMR + active" : ""}), purple dashed = active burn.
            Bars = intake − total burn: lime deficit, red surplus. Averages and totals count only days
            with both intake and burn logged.
          </p>
        </Card>

        <Card title="Weight & body fat">
          {weightRows.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={weightRows}>
                <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis yAxisId="w" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={40} />
                <YAxis yAxisId="bf" orientation="right" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={36} />
                <Tooltip {...TOOLTIP} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line yAxisId="w" dataKey="Weight" stroke="#a3e635" strokeWidth={2} dot={false} unit=" kg" />
                <Line yAxisId="bf" dataKey="Body fat %" stroke="#22d3ee" strokeWidth={2} dot={false} unit=" %" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-mist">Log measurements (or sync a Fitbit scale) to see weight trends.</p>
          )}
        </Card>

        <Card title="HRV & resting heart rate">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis {...AXES} width={36} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="HRV" stroke="#a3e635" strokeWidth={2} dot={false} connectNulls />
              <Line dataKey="Resting HR" stroke="#fb7185" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Sleep">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis {...AXES} width={36} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="Sleep (h)" stroke="#a78bfa" strokeWidth={2} dot={false} connectNulls />
              <Line dataKey="Sleep score" stroke="#22d3ee" strokeWidth={1.5} dot={false} connectNulls strokeDasharray="4 4" />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Steps & energy burned">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis yAxisId="s" {...AXES} width={48} />
              <YAxis yAxisId="k" orientation="right" {...AXES} width={40} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line yAxisId="s" dataKey="Steps" stroke="#a3e635" strokeWidth={2} dot={false} connectNulls />
              <Line yAxisId="k" dataKey="Burned kcal" stroke="#fb923c" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Calorie intake">
          {intakeRows.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={intakeRows}>
                <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis {...AXES} width={44} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(163,230,53,0.05)" }} />
                <Bar dataKey="kcal" fill="#a3e635" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-mist">No intake logged in this window.</p>
          )}
        </Card>

        <Card title="Workouts per week" className="lg:col-span-2">
          {workoutRows.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={workoutRows}>
                <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis allowDecimals={false} {...AXES} width={30} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(163,230,53,0.05)" }} />
                <Bar dataKey="sessions" fill="#22d3ee" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-mist">No workouts logged in this window.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
