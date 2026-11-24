// Typed client for the FastAPI backend. The Vite dev server proxies these paths,
// so the base URL is just "" (same origin).

export interface DayMetrics {
  date: string;
  hrv_ms: number | null;
  sleep_score: number | null;
  sleep_minutes: number | null;
  resting_hr_bpm: number | null;
  calories_kcal: number | null;
  steps: number | null;
  active_kcal: number | null;
  total_kcal: number | null;
  sleep_stages: Record<string, number> | null;
  hrv_baseline_ms: number | null;
  sleep_baseline: number | null;
  sleep_minutes_baseline: number | null;
  resting_hr_baseline_bpm: number | null;
  baseline_days: number;
}

export interface UserProfile {
  user_id: string;
  name: string;
  age: number | null;
  weight_kg: number | null;
  height_cm: number | null;
  goal: string;
  days: DayMetrics[];
}

export interface UserSummary {
  user_id: string;
  name: string;
  goal: string;
}

export interface Meal {
  id: number;
  date: string;
  meal: string;
  description: string;
  fatsecret_food_id: string | null;
  fatsecret_entry_id: string | null;
  servings: number;
  kcal: number;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  source: string;
}

export interface DayIntake {
  date: string;
  kcal: number;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  entries: number;
}

export interface MealTemplateItem {
  description: string;
  servings: number;
  kcal: number;
  protein_g?: number | null;
  carbs_g?: number | null;
  fat_g?: number | null;
  fiber_g?: number | null;
}

export interface MealTemplate {
  id: number;
  name: string;
  meal: string;
  items: MealTemplateItem[];
  total_kcal: number;
  updated_at: string;
}

export interface WorkoutTemplateExercise {
  name: string;
  sets: number;
  reps: string;
  weight_kg?: number | null;
  rpe_target?: string;
}

export interface WorkoutTemplate {
  id: number;
  name: string;
  exercises: WorkoutTemplateExercise[];
  updated_at: string;
}

export interface LoggedSet {
  reps?: number | null;
  weight_kg?: number | null;
  rpe?: number | null;
  rir?: number | null;
}

export interface Workout {
  id: number;
  date: string;
  program_id: number | null;
  session_name: string;
  exercises: { name: string; sets: LoggedSet[] }[];
  duration_min: number | null;
  notes: string | null;
  created_at: string;
}

export interface ResolvedItem {
  name: string;
  grams: number;
  kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  fiber_g: number;
  source: string; // "fatsecret" | "estimate" | "template" (client-side)
}

export interface MealCompute {
  items: ResolvedItem[];
  total_kcal: number;
  total_protein_g: number;
  total_carbs_g: number;
  total_fat_g: number;
  total_fiber_g: number;
}

export interface Run {
  id: number;
  date: string;
  distance_km: number;
  duration_min: number;
  avg_hr_bpm: number | null;
  source: string;
  notes: string | null;
  pace_min_per_km: number;
  est_5k_min: number;
}

export interface Program {
  id: number;
  kind: "training" | "nutrition";
  status: "draft" | "active" | "archived";
  version: number;
  name: string;
  summary: string;
  rationale: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  activated_at: string | null;
}

export interface Memory {
  id: number;
  kind: "preference" | "fact" | "constraint";
  key: string;
  value: string;
  source: "user" | "assistant";
  pinned: boolean;
  updated_at: string;
}

export interface Measurement {
  date: string;
  weight_kg: number | null;
  body_fat_pct: number | null;
  waist_cm: number | null;
  chest_cm: number | null;
  arm_cm: number | null;
  thigh_cm: number | null;
  source: string;
  notes: string | null;
}

export interface ExerciseSetOut {
  reps?: number | null;
  weight_kg?: number | null;
  rpe?: number | null;
  rir?: number | null;
}

export interface ExerciseHistoryRow {
  date: string;
  session_name: string;
  exercise: string;
  sets: ExerciseSetOut[];
  best_weight_kg: number | null;
  total_reps: number;
  volume_kg: number;
  est_1rm_kg: number | null;
}

export interface StrengthWeek {
  week_start: string;
  sets: number;
  total_reps: number;
  volume_kg: number;
  top_e1rm_kg: number | null;
}

export interface StrengthSummary {
  exercise: string;
  weekly: StrengthWeek[];
  pr_weight_kg: number | null;
  pr_reps: number | null;
  pr_date: string | null;
  best_e1rm_kg: number | null;
}

export interface UsageRow {
  model: string;
  purpose: string;
  calls: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
}

export interface Usage {
  days: number;
  by_model_purpose: UsageRow[];
  daily: { day: string; input_tokens: number; cached_tokens: number; output_tokens: number }[];
}

export interface IntegrationStatus {
  provider: string;
  configured: boolean;
  connected: boolean;
  last_synced_at: string | null;
  last_error: string | null;
}

export interface TodaySession {
  program_id: number | null;
  text: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

const TOKEN_KEY = "coach_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON error body */
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    if (payload && typeof payload === "object" && "detail" in payload) {
      const d = (payload as { detail: unknown }).detail;
      detail = Array.isArray(d)
        ? d.map((e) => (typeof e === "object" && e && "msg" in e ? String(e.msg) : String(e))).join("; ")
        : String(d);
    }
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export const api = {
  users: () => request<UserSummary[]>("GET", "/users"),
  user: (id: string) => request<UserProfile>("GET", `/users/${id}`),
  saveUser: (id: string, body: { name: string; age?: number | null; weight_kg?: number | null; height_cm?: number | null; goal?: string }) =>
    request<UserProfile>("PUT", `/users/${id}`, body),
  days: (id: string, days: number) => request<DayMetrics[]>("GET", `/users/${id}/days?days=${days}`),
  logDay: (id: string, date: string, body: Record<string, number>) =>
    request<DayMetrics>("PUT", `/users/${id}/days/${date}`, body),

  meals: (id: string, date?: string) =>
    request<Meal[]>("GET", `/users/${id}/meals${date ? `?day=${date}` : ""}`),
  logMeal: (id: string, body: Record<string, unknown>) => request<Meal>("POST", `/users/${id}/meals`, body),
  updateMeal: (id: string, mealId: number, body: Record<string, unknown>) =>
    request<Meal>("PUT", `/users/${id}/meals/${mealId}`, body),
  computeMeal: (id: string, items: { name: string; grams: number }[]) =>
    request<MealCompute>("POST", `/users/${id}/meals/compute`, { items }),
  deleteMeal: (id: string, mealId: number) => request<void>("DELETE", `/users/${id}/meals/${mealId}`),
  intake: (id: string, days: number) => request<DayIntake[]>("GET", `/users/${id}/intake?days=${days}`),

  mealTemplates: (id: string) => request<MealTemplate[]>("GET", `/users/${id}/meal-templates`),
  saveMealTemplate: (id: string, body: { name: string; meal: string; items: MealTemplateItem[] }) =>
    request<MealTemplate>("PUT", `/users/${id}/meal-templates`, body),
  deleteMealTemplate: (id: string, name: string) =>
    request<void>("DELETE", `/users/${id}/meal-templates/${encodeURIComponent(name)}`),
  logFromTemplate: (id: string, name: string, factor = 1, date?: string) =>
    request<Meal>("POST", `/users/${id}/meal-templates/${encodeURIComponent(name)}/log`, {
      factor,
      ...(date ? { date } : {}),
    }),

  workoutTemplates: (id: string) => request<WorkoutTemplate[]>("GET", `/users/${id}/workout-templates`),
  saveWorkoutTemplate: (id: string, body: { name: string; exercises: { name: string; sets: number; reps: string; weight_kg?: number | null; rpe_target?: string }[] }) =>
    request<WorkoutTemplate>("PUT", `/users/${id}/workout-templates`, body),
  deleteWorkoutTemplate: (id: string, name: string) =>
    request<void>("DELETE", `/users/${id}/workout-templates/${encodeURIComponent(name)}`),
  seedWorkoutTemplates: (id: string) => request<{ created: number }>("POST", `/users/${id}/workout-templates/seed`),

  workouts: (id: string, days = 30) => request<Workout[]>("GET", `/users/${id}/workouts?days=${days}`),
  logWorkout: (id: string, body: Record<string, unknown>) => request<Workout>("POST", `/users/${id}/workouts`, body),
  deleteWorkout: (id: string, workoutId: number) => request<void>("DELETE", `/users/${id}/workouts/${workoutId}`),

  runs: (id: string, days = 90) => request<Run[]>("GET", `/users/${id}/runs?days=${days}`),
  logRun: (id: string, body: Record<string, unknown>) => request<Run>("POST", `/users/${id}/runs`, body),
  deleteRun: (id: string, runId: number) => request<void>("DELETE", `/users/${id}/runs/${runId}`),
  todaySession: (id: string) => request<TodaySession>("GET", `/users/${id}/session/today`),
  exerciseNames: (id: string, days = 90) => request<string[]>("GET", `/users/${id}/exercises?days=${days}`),
  exerciseHistory: (id: string, name: string, days = 90) =>
    request<ExerciseHistoryRow[]>("GET", `/users/${id}/exercises/history?name=${encodeURIComponent(name)}&days=${days}`),
  strengthSummary: (id: string, name: string, weeks = 8) =>
    request<StrengthSummary>("GET", `/users/${id}/exercises/strength?name=${encodeURIComponent(name)}&weeks=${weeks}`),

  programs: (id: string, status?: string) =>
    request<Program[]>("GET", `/users/${id}/programs${status ? `?status=${status}` : ""}`),
  generateProgram: (id: string, kind: string, brief?: string) =>
    request<Program>("POST", `/users/${id}/programs/generate`, { kind, brief: brief || null }),
  planWeek: (id: string, brief?: string) =>
    request<{ training: Program; nutrition: Program }>("POST", `/users/${id}/plans/weekly`, { brief: brief || null }),
  reviseProgram: (id: string, programId: number, feedback: string) =>
    request<Program>("POST", `/users/${id}/programs/${programId}/revise`, { feedback }),
  activateProgram: (id: string, programId: number) =>
    request<Program>("POST", `/users/${id}/programs/${programId}/activate`),
  reviewProgram: (id: string, programId: number, regenerate = false) =>
    request<{ program_id: number; assessment: string; regenerated_version: number | null }>(
      "POST",
      `/users/${id}/programs/${programId}/review?regenerate=${regenerate}`
    ),

  measurements: (id: string, days = 90) => request<Measurement[]>("GET", `/users/${id}/measurements?days=${days}`),
  saveMeasurement: (id: string, date: string, body: Record<string, unknown>) =>
    request<Measurement>("PUT", `/users/${id}/measurements/${date}`, body),

  memories: (id: string) => request<Memory[]>("GET", `/users/${id}/memories`),
  saveMemory: (id: string, body: { kind: string; key: string; value: string; pinned?: boolean }) =>
    request<Memory[]>("PUT", `/users/${id}/memories`, body),
  deleteMemory: (id: string, kind: string, key: string) =>
    request<void>("DELETE", `/users/${id}/memories/${kind}/${key}`),

  usage: (id: string, days = 30) => request<Usage>("GET", `/users/${id}/usage?days=${days}`),

  integrations: (id: string) => request<IntegrationStatus[]>("GET", `/users/${id}/integrations`),
  connect: (id: string, provider: string) =>
    request<{ authorization_url: string }>("POST", `/users/${id}/integrations/${provider}/connect`),
  disconnect: (id: string, provider: string) => request<void>("DELETE", `/users/${id}/integrations/${provider}`),
  sync: (id: string, days = 14) =>
    request<{ provider: string; days_updated: number; error: string | null; reconnect: boolean; runs_new: number }[]>(
      "POST",
      `/users/${id}/sync?days=${days}`
    ),
};
