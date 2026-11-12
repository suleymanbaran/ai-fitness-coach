export const STEP_GOAL = 10_000;

export interface GridDay {
  name?: string;
  focus?: string;
  day_type?: string;
  cardio?: string;
  exercises?: { name?: string; sets?: number; reps?: string; weight_guidance?: string; rpe_target?: string }[];
}

// Big one-word session label, extracted from the day's focus text.
export function dayKind(day: GridDay): { label: string; className: string } {
  const f = (day.focus ?? "").toLowerCase();
  if (day.day_type === "rest") return { label: "Rest", className: "text-ash" };
  if (f.includes("push")) return { label: "Push", className: "text-lime" };
  if (f.includes("pull")) return { label: "Pull", className: "text-cyan" };
  if (f.includes("leg") || f.includes("bacak")) return { label: "Legs", className: "text-violet" };
  if (f.includes("upper") || f.includes("üst")) return { label: "Upper", className: "text-lime" };
  if (f.includes("lower") || f.includes("alt")) return { label: "Lower", className: "text-violet" };
  if (f.includes("full")) return { label: "Full body", className: "text-amber" };
  if (f.includes("core") || f.includes("karın") || f.includes("ab ")) return { label: "Core", className: "text-magenta" };
  if (day.day_type === "cardio") return { label: "Cardio", className: "text-lime" };
  if (day.day_type === "mixed") return { label: "Mixed", className: "text-violet" };
  return { label: day.focus?.split(" ")[0] || "Training", className: "text-cloud" };
}
