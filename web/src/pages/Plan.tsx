import { useState } from "react";
import { CalendarDays, Flame, Footprints, HeartPulse, MessageCircle } from "lucide-react";
import { api, Program } from "../api";
import { useFetch } from "../hooks";
import { STEP_GOAL, dayKind, GridDay } from "../planDays";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner } from "../components/ui";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TYPE_TONES: Record<string, "lime" | "amber" | "zinc" | "red"> = {
  strength: "lime",
  cardio: "amber",
  mixed: "lime",
  rest: "zinc",
};

function WeeklyGrid({ program, nutrition }: { program: Program; nutrition?: Program | null }) {
  const weeks = (program.payload.weeks ?? []) as { focus?: string; days?: GridDay[] }[];
  const [week, setWeek] = useState(0);
  const current = weeks[Math.min(week, weeks.length - 1)];
  if (!current) return <Empty>This plan has no weeks.</Empty>;

  const nt = (nutrition?.payload ?? {}) as Record<string, unknown>;
  const num = (key: string): number | null => (typeof nt[key] === "number" ? (nt[key] as number) : null);
  const kcal = num("target_kcal");

  return (
    <div>
      {weeks.length > 1 && (
        <div className="mb-4 flex gap-1">
          {weeks.map((_unused, i) => (
            <button
              key={i}
              onClick={() => setWeek(i)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                i === week ? "bg-grad-primary font-bold text-night" : "bg-white/5 text-mist hover:text-cloud"
              }`}
            >
              Week {i + 1}
            </button>
          ))}
        </div>
      )}
      <p className="mb-3 text-xs font-medium text-mist">Focus: {current.focus || "—"}</p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        {(current.days ?? []).slice(0, 7).map((day, i) => {
          const kind = dayKind(day);
          return (
            <div key={i} className="flex flex-col rounded-xl border border-edge bg-night-3 p-3">
              <div className="flex items-baseline justify-between gap-1">
                <span className="text-[10px] uppercase tracking-wider text-mist">
                  {day.name ? day.name.split("—")[0].trim() : DAY_LABELS[i]}
                </span>
                <Badge tone={TYPE_TONES[day.day_type ?? "strength"] ?? "zinc"}>{day.day_type ?? "strength"}</Badge>
              </div>
              <div className={`font-grotesk mt-1 text-lg font-bold leading-tight ${kind.className}`}>{kind.label}</div>
              {day.focus && <div className="mt-0.5 text-[11px] leading-snug text-ash">{day.focus}</div>}

              {kcal != null && (
                <div className="mt-2 rounded-lg bg-white/5 px-2 py-1.5">
                  <div className="flex items-center gap-1 text-xs font-semibold text-cloud">
                    <Flame className="h-3.5 w-3.5 text-ember" />
                    {kcal.toLocaleString()} kcal
                  </div>
                  <div className="mt-0.5 text-[10px] tabular-nums text-mist">
                    P {num("protein_g") ?? "—"} · C {num("carbs_g") ?? "—"} · F {num("fat_g") ?? "—"}
                    {num("fiber_g") != null && ` · Fiber ${num("fiber_g")}`}
                  </div>
                  <div className="mt-1 flex items-center gap-1 text-[10px] tabular-nums text-mist">
                    <Footprints className="h-3 w-3 text-cyan" />
                    {STEP_GOAL.toLocaleString()} steps
                  </div>
                </div>
              )}

              {day.cardio ? (
                <p className="mt-2 flex items-start gap-1 text-xs font-medium text-lime">
                  <HeartPulse className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {day.cardio}
                </p>
              ) : null}
              <ul className="mt-2 space-y-1">
                {(day.exercises ?? []).map((e, j) => (
                  <li key={j} className="text-xs text-mist">
                    {e.name}{" "}
                    <span className="text-mist">
                      {e.sets}x{e.reps}
                      {e.weight_guidance ? ` · ${e.weight_guidance}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Plan({ userId }: { userId: string }) {
  const programs = useFetch(() => api.programs(userId), [userId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [reviseKind, setReviseKind] = useState("training");
  const [reviewText, setReviewText] = useState("");

  if (programs.loading) return <Spinner />;

  const all = programs.data ?? [];
  const activeTraining = all.find((p) => p.status === "active" && p.kind === "training");
  const activeNutrition = all.find((p) => p.status === "active" && p.kind === "nutrition");


  const revise = async () => {
    const active = reviseKind === "training" ? activeTraining : activeNutrition;
    if (!active || !feedback.trim()) return;
    setBusy(true);
    setError("");
    try {
      const revised = await api.reviseProgram(userId, active.id, feedback.trim());
      setFeedback("");
      setReviewText(`Revised to v${revised.version}: ${revised.summary}`);
      programs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revision failed");
    } finally {
      setBusy(false);
    }
  };

  const review = async () => {
    if (!activeTraining) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.reviewProgram(userId, activeTraining.id, false);
      setReviewText(r.assessment);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="Weekly plan" subtitle="One plan covering training, cardio and nutrition — revised whenever life gets in the way." />

      <Card title="Planning happens on Telegram" className="mb-4">
        <p className="flex items-start gap-2 text-sm text-mist">
          <MessageCircle className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
          <span>
            Ask the coach for a plan — send <code className="rounded bg-white/5 px-1">/plan</code> or just say
            "make me a weekly plan, 4 days, knee-friendly". It designs all 7 days (strength + cardio +
            numeric nutrition targets), activates them, and the plan shows up here and on the
            calendar automatically. Changes: tell the coach in chat, or use Revise below.
          </span>
        </p>
      </Card>

      {activeTraining && (
        <Card
          title={`Training: ${activeTraining.name} (v${activeTraining.version})`}
          className="mb-4"
          action={
            <Button onClick={review} disabled={busy}>
              Review adherence
            </Button>
          }
        >
          <WeeklyGrid program={activeTraining} nutrition={activeNutrition} />
        </Card>
      )}

      {activeNutrition && (
        <Card title={`Nutrition: ${activeNutrition.name} (v${activeNutrition.version})`} className="mb-4">
          <p className="text-sm text-mist">{activeNutrition.summary}</p>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center sm:grid-cols-6">
            {([
              ["kcal", activeNutrition.payload.target_kcal],
              ["Protein", activeNutrition.payload.protein_g],
              ["Carbs", activeNutrition.payload.carbs_g],
              ["Fat", activeNutrition.payload.fat_g],
              ["Fiber g", activeNutrition.payload.fiber_g],
              ["Sodium mg", activeNutrition.payload.sodium_mg],
            ] as [string, unknown][]).map(([label, value]) => (
              <div key={label} className="rounded-lg bg-white/5 p-2">
                <div className="text-sm font-bold text-cloud">{typeof value === "number" ? value : "—"}</div>
                <div className="text-[10px] uppercase text-mist">{label}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Can't adhere? Revise it" className="mb-4">
        <div className="flex flex-col gap-3 sm:flex-row">
          <Select value={reviseKind} onChange={(e) => setReviseKind(e.target.value)} className="sm:w-40">
            <option value="training">Training</option>
            <option value="nutrition">Nutrition</option>
          </Select>
          <Input
            placeholder="What doesn't work? e.g. 'Mondays no longer work, move it to Saturday' or 'no dairy'"
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
          />
          <Button
            variant="primary"
            onClick={revise}
            disabled={busy || !feedback.trim() || !(reviseKind === "training" ? activeTraining : activeNutrition)}
            className="shrink-0"
          >
            {busy ? "Revising..." : "Revise plan"}
          </Button>
        </div>
        {error && <p className="mt-2 text-sm text-ember">{error}</p>}
        <p className="mt-2 text-xs text-ash">
          Creates the next version and activates it — you can also just tell the coach in chat or on Telegram.
        </p>
        {reviewText && (
          <div className="prose prose-sm mt-3 max-w-none rounded-xl border border-edge bg-night-3 p-3 text-sm text-cloud">
            {reviewText}
          </div>
        )}
      </Card>

      <Card title="Version history">
        {all.length ? (
          <ul className="divide-y divide-edge">
            {all.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <span className="text-sm text-cloud">
                    <CalendarDays className="mr-1.5 inline h-3.5 w-3.5 text-mist" />
                    {p.kind} v{p.version} — {p.name}
                  </span>
                  <p className="truncate text-xs text-mist">{p.summary}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={p.status === "active" ? "lime" : p.status === "draft" ? "amber" : "zinc"}>{p.status}</Badge>
                  {p.status === "draft" && (
                    <Button
                      onClick={async () => {
                        await api.activateProgram(userId, p.id);
                        programs.reload();
                      }}
                    >
                      Activate
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No plans yet — generate your first weekly plan above.</Empty>
        )}
      </Card>
      {programs.error && <div className="mt-4"><ErrorNote error={programs.error} /></div>}
    </div>
  );
}
