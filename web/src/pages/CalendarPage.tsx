import { useState } from "react";
import { Check, ChevronLeft, ChevronRight, Dumbbell, HeartPulse, X } from "lucide-react";
import { api, DayIntake, DayMetrics } from "../api";
import { todayISO, useFetch } from "../hooks";
import { STEP_GOAL, dayKind, GridDay } from "../planDays";
import { Badge, Button, Card, ErrorNote, PageHeader, Spinner } from "../components/ui";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const TR_MONTHS = ["oca", "şub", "mar", "nis", "may", "haz", "tem", "ağu", "eyl", "eki", "kas", "ara"];
const EN_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

// Plans revised mid-week label their days with real dates ("Salı, 17 Kasım — 1. gün");
// when present, match by date instead of assuming a Monday-first order.
function parsePlanDate(name?: string): { month: number; day: number } | null {
  if (!name) return null;
  const s = name.toLowerCase();
  const monthIndex = (token: string) => Math.max(TR_MONTHS.indexOf(token), EN_MONTHS.indexOf(token));
  let m = s.match(/(\d{1,2})\s+([a-zçğıöşü]{3,9})/);
  if (m) {
    const i = monthIndex(m[2].slice(0, 3));
    if (i >= 0) return { month: i + 1, day: Number(m[1]) };
  }
  m = s.match(/([a-zçğıöşü]{3,9})\.?\s+(\d{1,2})/);
  if (m) {
    const i = monthIndex(m[1].slice(0, 3));
    if (i >= 0) return { month: i + 1, day: Number(m[2]) };
  }
  return null;
}

export default function CalendarPage({ userId }: { userId: string }) {
  const today = todayISO();
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selected, setSelected] = useState(today);

  const intake = useFetch(() => api.intake(userId, 365), [userId]);
  const workouts = useFetch(() => api.workouts(userId, 365), [userId]);
  const runs = useFetch(() => api.runs(userId, 365), [userId]);
  const metrics = useFetch(() => api.days(userId, 365), [userId]);
  const programs = useFetch(() => api.programs(userId, "active"), [userId]);

  const loading = intake.loading || workouts.loading || runs.loading || metrics.loading || programs.loading;
  const error = intake.error || workouts.error || runs.error || metrics.error || programs.error;

  const active = programs.data ?? [];
  const nutrition = active.find((p) => p.kind === "nutrition");
  const training = active.find((p) => p.kind === "training");
  const num = (key: string): number | null =>
    typeof nutrition?.payload?.[key] === "number" ? (nutrition.payload[key] as number) : null;
  const targetKcal = num("target_kcal");
  const weeks = (training?.payload?.weeks ?? []) as { days?: GridDay[] }[];
  const planDays = weeks[0]?.days ?? [];
  const planDates = planDays.map((d) => parsePlanDate(d.name));
  const planHasDates = planDates.some((p) => p !== null);

  const planDayFor = (d: Date): GridDay | null => {
    if (planDays.length !== 7) return null;
    if (planHasDates) {
      const i = planDates.findIndex((p) => p !== null && p.month === d.getMonth() + 1 && p.day === d.getDate());
      return i >= 0 ? planDays[i] : null;
    }
    return planDays[(d.getDay() + 6) % 7];
  };

  const intakeByDate = new Map<string, DayIntake>((intake.data ?? []).map((d) => [d.date, d]));
  const metricsByDate = new Map<string, DayMetrics>((metrics.data ?? []).map((d) => [d.date, d]));
  const runDates = new Set((runs.data ?? []).map((r) => r.date));
  const workoutDates = new Set((workouts.data ?? []).map((w) => w.date));

  // 42-cell grid starting on the Monday of the week containing the 1st.
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const cells = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
  const monthName = cursor.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const isCurrentMonth = iso(cursor) === new Date().toISOString().slice(0, 7) + "-01";

  const shift = (delta: number) => setCursor((c) => new Date(c.getFullYear(), c.getMonth() + delta, 1));

  // ---- selected-day detail ----
  const selDate = new Date(`${selected}T00:00:00`);
  const selPlan = planDayFor(selDate);
  const selIntake = intakeByDate.get(selected);
  const selMetrics = metricsByDate.get(selected);
  const selWorkouts = (workouts.data ?? []).filter((w) => w.date === selected);
  const selRuns = (runs.data ?? []).filter((r) => r.date === selected);
  const selType = selPlan?.day_type ?? null;
  const selPlanned = selType === "strength" || selType === "mixed" || selType === "cardio";
  const selCardioPlanned = Boolean(selPlan?.cardio) || selType === "cardio" || selType === "mixed";
  const selHasWorkout = selWorkouts.length > 0;
  const selHasRun = selRuns.length > 0;

  const targetRow = (label: string, actual: number | null | undefined, target: number | null, unit = "g") => (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span className="text-mist">{label}</span>
      <span className="tabular-nums">
        <span className={actual == null ? "text-ash" : "text-cloud"}>
          {actual != null ? Math.round(actual).toLocaleString() : "—"}
        </span>
        {target != null && <span className="text-ash"> / {target.toLocaleString()} {unit}</span>}
      </span>
    </div>
  );

  const steps = selMetrics?.steps ?? null;
  const stepsPct = steps != null ? steps / STEP_GOAL : null;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Calendar"
        subtitle="Plan vs. what actually happened — click any day for steps, nutrition targets and the planned session."
      />
      {error && <ErrorNote error={error} />}
      {loading && <Spinner />}

      <Card
        title={monthName}
        action={
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => shift(-1)} className="px-2">
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                const now = new Date();
                setCursor(new Date(now.getFullYear(), now.getMonth(), 1));
                setSelected(today);
              }}
              disabled={isCurrentMonth}
            >
              Today
            </Button>
            <Button variant="ghost" onClick={() => shift(1)} className="px-2" disabled={isCurrentMonth}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        }
      >
        <div className="grid grid-cols-7 gap-1.5">
          {DAY_LABELS.map((d) => (
            <div key={d} className="pb-1 text-center text-xs font-medium text-ash">
              {d}
            </div>
          ))}
          {cells.map((d) => {
            const dateStr = iso(d);
            const inMonth = d.getMonth() === cursor.getMonth();
            const isToday = dateStr === today;
            const isFuture = dateStr > today;
            const planDay = planDayFor(d);
            const dayType = planDay?.day_type ?? null;
            const kind = planDay ? dayKind(planDay) : null;
            const kcal = intakeByDate.get(dateStr)?.kcal;
            const stepsDay = metricsByDate.get(dateStr)?.steps;
            const hasWorkout = workoutDates.has(dateStr);
            const hasRun = runDates.has(dateStr);
            const plannedCardio = Boolean(planDay?.cardio) || dayType === "cardio" || dayType === "mixed";
            const planned = dayType === "strength" || dayType === "mixed" || dayType === "cardio";
            const missed = planned && !isFuture && !hasWorkout && !hasRun;

            let kcalClass = "text-mist";
            if (kcal != null && targetKcal != null) {
              kcalClass = kcal <= targetKcal * 1.05 ? "text-lime" : kcal <= targetKcal * 1.15 ? "text-amber" : "text-magenta";
            }

            return (
              <button
                key={dateStr}
                type="button"
                onClick={() => setSelected(dateStr)}
                className={`relative min-h-[104px] rounded-lg border p-1.5 text-left transition-colors hover:border-mist/40 ${
                  dateStr === selected
                    ? "border-cyan/60 bg-night-3"
                    : isToday
                      ? "border-lime/50 bg-night-3"
                      : "border-edge bg-night"
                } ${inMonth ? "" : "opacity-35"}`}
              >
                <div className="flex items-baseline justify-between gap-1">
                  <span className={`text-xs font-semibold ${isToday ? "text-grad font-grotesk" : "text-mist"}`}>
                    {d.getDate()}
                  </span>
                  {kind && (
                    <span className={`truncate text-[10px] font-bold ${kind.className}`}>{kind.label}</span>
                  )}
                </div>
                {(kcal != null || (planDay != null && targetKcal != null)) && (
                  <div className={`mt-1 text-[11px] tabular-nums ${kcalClass}`} title="kcal taken / target">
                    {kcal != null ? (
                      <>
                        {kcal.toLocaleString()}
                        {targetKcal != null && <span className="text-ash">/{targetKcal.toLocaleString()}</span>}
                      </>
                    ) : (
                      <span className="text-ash">{targetKcal!.toLocaleString()} kcal</span>
                    )}
                  </div>
                )}
                {(stepsDay != null || planDay != null) && (
                  <div
                    className={`mt-0.5 text-[10px] tabular-nums ${stepsDay != null && stepsDay >= STEP_GOAL ? "text-cyan" : "text-ash"}`}
                    title="steps / goal"
                  >
                    {stepsDay != null
                      ? `${(stepsDay / 1000).toFixed(1)}k/${STEP_GOAL / 1000}k`
                      : `goal ${STEP_GOAL / 1000}k steps`}
                  </div>
                )}
                {plannedCardio && (
                  <div className={`mt-0.5 flex items-center gap-0.5 text-[10px] font-medium ${hasRun ? "text-lime" : "text-lime/70"}`}>
                    <HeartPulse className="h-3 w-3" /> cardio{hasRun && <Check className="h-3 w-3" />}
                  </div>
                )}
                <div className="absolute inset-x-1.5 bottom-1.5 flex items-center gap-1">
                  {hasWorkout && (
                    <span className="flex items-center gap-0.5 text-[10px] font-medium text-lime">
                      <Check className="h-3 w-3" /> <Dumbbell className="h-3 w-3" />
                    </span>
                  )}
                  {missed && (
                    <span className="flex items-center gap-0.5 text-[10px] text-magenta">
                      <X className="h-3 w-3" /> planned
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-edge pt-3 text-xs text-ash">
          <span><span className="text-lime">✓</span> workout or run logged</span>
          <span><span className="text-magenta">✗ planned</span> session missed</span>
          <span><span className="text-lime">♥ cardio</span> planned — with ✓ when a run was logged</span>
          <span>kcal <span className="text-lime">green</span> ≤ target, <span className="text-amber">amber</span> ≤ +15%, <span className="text-magenta">red</span> above</span>
          {planDays.length !== 7 && <span>Ask for a weekly plan on Telegram to see day types here.</span>}
        </div>
      </Card>

      <Card
        title={selDate.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }) + (selected === today ? " — today" : "")}
        action={selType ? <Badge tone={selType === "rest" ? "zinc" : "lime"}>{selType}</Badge> : undefined}
      >
        <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-edge pb-3 text-xs">
          <span className="font-semibold text-ash">Adherence:</span>
          {selPlanned ? (
            selHasWorkout || selHasRun ? (
              <span className="rounded-full bg-lime/15 px-2 py-0.5 font-medium text-lime">session ✓</span>
            ) : selected <= today ? (
              <span className="rounded-full bg-magenta/15 px-2 py-0.5 font-medium text-magenta">session missed</span>
            ) : (
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-mist">session scheduled</span>
            )
          ) : (
            <span className="rounded-full bg-white/5 px-2 py-0.5 text-ash">no session planned</span>
          )}
          {selCardioPlanned &&
            (selHasRun ? (
              <span className="rounded-full bg-lime/15 px-2 py-0.5 font-medium text-lime">cardio ✓</span>
            ) : selected <= today ? (
              <span className="rounded-full bg-magenta/15 px-2 py-0.5 font-medium text-magenta">cardio missed</span>
            ) : (
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-mist">cardio scheduled</span>
            ))}
          {steps != null && (
            <span
              className={`rounded-full px-2 py-0.5 font-medium ${
                steps >= STEP_GOAL ? "bg-lime/15 text-lime" : "bg-white/5 text-mist"
              }`}
            >
              steps {Math.round((steps / STEP_GOAL) * 100)}%
            </span>
          )}
          {selIntake && targetKcal != null && (
            <span
              className={`rounded-full px-2 py-0.5 font-medium ${
                selIntake.kcal <= targetKcal ? "bg-lime/15 text-lime" : "bg-magenta/15 text-magenta"
              }`}
            >
              {selIntake.kcal <= targetKcal
                ? `${(targetKcal - selIntake.kcal).toLocaleString()} kcal under target`
                : `${(selIntake.kcal - targetKcal).toLocaleString()} kcal over target`}
            </span>
          )}
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs font-semibold text-ash">Planned</h3>
            {selPlan ? (
              <div className="space-y-1.5 text-sm">
                {selPlan.focus && <p className="font-medium text-cloud">{selPlan.focus}</p>}
                {selPlan.cardio && (
                  <p className="text-cyan">Cardio: {selPlan.cardio}</p>
                )}
                {!selPlan.cardio && selType !== "rest" && <p className="text-ash">No cardio scheduled.</p>}
                {Array.isArray(selPlan.exercises) && selPlan.exercises.length > 0 && (
                  <p className="text-mist">
                    {selPlan.exercises.map((e) => e.name).filter(Boolean).join(" · ")}
                  </p>
                )}
                {selType === "rest" && <p className="text-ash">Rest day.</p>}
              </div>
            ) : (
              <p className="text-sm text-ash">Nothing planned (no weekly plan covers this day).</p>
            )}
            <div className="mt-3 space-y-1.5">
              <h3 className="text-xs font-semibold text-ash">Done</h3>
              {selWorkouts.length === 0 && selRuns.length === 0 && (
                <p className="text-sm text-ash">
                  {selPlanned && selected <= today ? "No session logged — missed." : "No workout or run logged."}
                </p>
              )}
              {selWorkouts.map((w) => (
                <p key={w.id} className="flex items-center gap-1.5 text-sm text-lime">
                  <Dumbbell className="h-3.5 w-3.5" /> {w.session_name}
                  {w.duration_min ? <span className="text-ash">· {w.duration_min} min</span> : null}
                </p>
              ))}
              {selRuns.map((r) => (
                <p key={r.id} className="text-sm text-cyan">
                  Run · {r.distance_km} km in {Math.floor(r.duration_min)}:{String(Math.round((r.duration_min % 1) * 60)).padStart(2, "0")}
                  {r.avg_hr_bpm ? ` · HR ${r.avg_hr_bpm}` : ""}
                </p>
              ))}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-semibold text-ash">Steps</h3>
            <p className="text-sm tabular-nums">
              <span className={stepsPct == null ? "text-ash" : stepsPct >= 1 ? "text-lime" : stepsPct >= 0.7 ? "text-amber" : "text-magenta"}>
                {steps != null ? steps.toLocaleString() : "—"}
              </span>
              <span className="text-ash"> / {STEP_GOAL.toLocaleString()} goal</span>
            </p>
            <h3 className="mb-2 mt-4 text-xs font-semibold text-ash">Nutrition (taken / target)</h3>
            <div className="space-y-1">
              {targetRow("Calories", selIntake?.kcal, targetKcal, "kcal")}
              {targetRow("Protein", selIntake?.protein_g, num("protein_g"))}
              {targetRow("Carbs", selIntake?.carbs_g, num("carbs_g"))}
              {targetRow("Fat", selIntake?.fat_g, num("fat_g"))}
              {targetRow("Fiber", selIntake?.fiber_g, num("fiber_g"))}
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="text-mist">Sodium</span>
                <span className="tabular-nums text-ash">{num("sodium_mg") != null ? `≤ ${num("sodium_mg")!.toLocaleString()} mg` : "—"}</span>
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
