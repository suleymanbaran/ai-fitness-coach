import { useState } from "react";
import { Flame, RefreshCw } from "lucide-react";
import { api } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Spinner, Stat } from "../components/ui";
import { adjustedTotalBurn, TOTAL_BURN_FACTOR } from "../calories";

function CalorieRing({ eaten, target }: { eaten: number; target: number | null }) {
  const size = 168;
  const stroke = 14;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const fraction = target ? Math.min(eaten / target, 1) : 0;
  const over = target != null && eaten > target;
  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#a3e635" />
            <stop offset="100%" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#2c3540" strokeWidth={stroke} />
        {target && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={over ? "#fb7185" : "url(#ringGrad)"}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${fraction * circumference} ${circumference}`}
          />
        )}
      </svg>
      <div className="absolute flex flex-col items-center">
        <Flame className="mb-1 h-5 w-5 text-ember" />
        <span className="font-grotesk text-3xl font-bold tabular-nums text-cloud">{eaten.toLocaleString()}</span>
        <span className="text-xs text-mist">{target ? `of ${target.toLocaleString()} kcal` : "kcal today"}</span>
      </div>
    </div>
  );
}

export default function Today({ userId }: { userId: string }) {
  const today = todayISO();
  const profile = useFetch(() => api.user(userId), [userId]);
  const intake = useFetch(() => api.intake(userId, 1), [userId]);
  const meals = useFetch(() => api.meals(userId, today), [userId]);
  const session = useFetch(() => api.todaySession(userId), [userId]);
  const programs = useFetch(() => api.programs(userId, "active"), [userId]);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");

  const nutritionTarget = (() => {
    const program = (programs.data ?? []).find((p) => p.kind === "nutrition");
    const target = program?.payload?.target_kcal;
    return typeof target === "number" ? target : null;
  })();

  const day = profile.data?.days?.[profile.data.days.length - 1] ?? null;
  const eatenToday = (intake.data ?? []).find((d) => d.date === today)?.kcal ?? day?.calories_kcal ?? 0;

  const sync = async () => {
    setSyncing(true);
    setSyncMsg("");
    try {
      const results = await api.sync(userId, 7);
      setSyncMsg(
        results
          .map((r) => `${r.provider}: ${r.error ?? `${r.days_updated} days${r.runs_new ? ` + ${r.runs_new} run${r.runs_new > 1 ? "s" : ""}` : ""}`}`)
          .join(" · ") || "No providers connected"
      );
      profile.reload();
      intake.reload();
    } catch (err) {
      setSyncMsg(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  if (profile.loading) return <Spinner />;
  if (profile.error) return <ErrorNote error={profile.error} />;

  const fmt = (v: number | null | undefined, digits = 0) => (v == null ? "—" : v.toFixed(digits));
  const delta = (v: number | null | undefined, base: number | null | undefined, invert = false) => {
    if (v == null || base == null || base === 0) return { text: undefined, good: null as boolean | null };
    const diff = v - base;
    const good = invert ? diff <= 0 : diff >= 0;
    return { text: `${diff > 0 ? "+" : ""}${diff.toFixed(1)} vs baseline`, good };
  };

  const hrvDelta = delta(day?.hrv_ms, day?.hrv_baseline_ms);
  const rhrDelta = delta(day?.resting_hr_bpm, day?.resting_hr_baseline_bpm, true);
  const sleepHours = day?.sleep_minutes != null ? day.sleep_minutes / 60 : null;

  return (
    <div>
      <PageHeader
        title="Today"
        subtitle={new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
      >
        <Button onClick={sync} disabled={syncing}>
          <RefreshCw className={`mr-1.5 inline h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
          {syncing ? "Syncing..." : "Sync wearables"}
        </Button>
      </PageHeader>
      {syncMsg && <p className="mb-4 text-sm text-mist">{syncMsg}</p>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-7">
        <Stat label="HRV" value={fmt(day?.hrv_ms, 1)} unit="ms" delta={hrvDelta.text} deltaGood={hrvDelta.good} />
        <Stat label="Resting HR" value={fmt(day?.resting_hr_bpm)} unit="bpm" delta={rhrDelta.text} deltaGood={rhrDelta.good} />
        <Stat
          label="Sleep"
          value={sleepHours != null ? sleepHours.toFixed(1) : fmt(day?.sleep_score)}
          unit={sleepHours != null ? "h" : day?.sleep_score != null ? "/100" : undefined}
        />
        <Stat label="Steps" value={day?.steps != null ? day.steps.toLocaleString() : "—"} />
        <Stat label="Active burn" value={fmt(day?.active_kcal)} unit="kcal" />
        {/* Same 10% discount as the Trends energy balance. */}
        <Stat
          label="Total burn"
          value={adjustedTotalBurn(day?.total_kcal ?? null)?.toLocaleString() ?? "—"}
          unit="kcal"
          delta={`Fitbit ×${TOTAL_BURN_FACTOR}, incl. BMR`}
          deltaGood={null}
        />
        <Stat label="Weight" value={fmt(profile.data?.weight_kg, 1)} unit="kg" />
      </div>

      {day?.sleep_stages && (
        <p className="mt-3 text-sm text-mist">
          Last night's sleep stages:{" "}
          <span className="text-violet">deep {day.sleep_stages.deep ?? 0}m</span> ·{" "}
          <span className="text-cyan">REM {day.sleep_stages.rem ?? 0}m</span> ·{" "}
          light {day.sleep_stages.light ?? 0}m · awake {day.sleep_stages.awake ?? 0}m
        </p>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Energy">
          <div className="flex justify-center py-2">
            <CalorieRing eaten={eatenToday} target={nutritionTarget} />
          </div>
          {!nutritionTarget && (
            <p className="text-center text-xs text-mist">Activate a nutrition program to see a target ring.</p>
          )}
        </Card>

        <Card title="Today's session" className="lg:col-span-2">
          {session.loading ? (
            <Spinner label="" />
          ) : session.data ? (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-cloud">{session.data.text}</pre>
          ) : (
            <Empty>Could not load the session.</Empty>
          )}
        </Card>
      </div>

      <Card title="Today's meals" className="mt-4">
        {meals.data && meals.data.length > 0 ? (
          <ul className="divide-y divide-edge">
            {meals.data.map((m) => (
              <li key={m.id} className="flex items-center justify-between py-2.5">
                <div>
                  <span className="text-sm font-medium text-cloud">{m.description}</span>
                  <span className="ml-2 text-xs text-mist">{m.meal}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-lime">{m.kcal} kcal</span>
                  <Badge tone={m.source === "assistant" ? "amber" : "zinc"}>{m.source}</Badge>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>Nothing logged yet — add meals on the Nutrition page or text the coach on Telegram.</Empty>
        )}
      </Card>
    </div>
  );
}
