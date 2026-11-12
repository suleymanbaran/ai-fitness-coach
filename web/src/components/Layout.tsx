import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  Brain,
  CalendarDays,
  CalendarRange,
  Coins,
  Dumbbell,
  HeartPulse,
  LayoutDashboard,
  Ruler,
  Settings,
  TrendingUp,
  Utensils,
} from "lucide-react";
import { UserSummary } from "../api";

const NAV = [
  { to: "/", label: "Today", icon: LayoutDashboard, end: true },
  { to: "/trends", label: "Trends", icon: TrendingUp },
  { to: "/plan", label: "Plan", icon: CalendarDays },
  { to: "/calendar", label: "Calendar", icon: CalendarRange },
  { to: "/nutrition", label: "Nutrition", icon: Utensils },
  { to: "/workouts", label: "Workouts", icon: Dumbbell },
  { to: "/cardio", label: "Cardio", icon: HeartPulse },
  { to: "/measures", label: "Measures", icon: Ruler },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/cost", label: "Cost", icon: Coins },
  { to: "/settings", label: "Settings", icon: Settings },
];

function navClass(isActive: boolean) {
  return `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all ${
    isActive
      ? "bg-grad-primary font-bold text-night shadow-[0_0_18px_rgba(163,230,53,0.25)]"
      : "font-medium text-mist hover:bg-white/5 hover:text-cloud"
  }`;
}

function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2 px-2">
      <span className="bg-grad-primary flex h-8 w-8 items-center justify-center rounded-lg">
        <Activity className="h-5 w-5 text-night" strokeWidth={2.5} />
      </span>
      <span className={`text-grad font-grotesk font-bold tracking-tight ${compact ? "text-lg" : "text-xl"}`}>
        coach
      </span>
    </div>
  );
}

export default function Layout({
  users,
  userId,
  onUserChange,
}: {
  users: UserSummary[];
  userId: string;
  onUserChange: (id: string) => void;
}) {
  return (
    <div className="flex min-h-screen">
      {/* Sidebar (desktop) */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-56 flex-col border-r border-edge bg-night-2 p-4 md:flex">
        <div className="mb-8">
          <Wordmark />
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => navClass(isActive)}>
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        {users.length > 1 && (
          <select
            value={userId}
            onChange={(e) => onUserChange(e.target.value)}
            className="rounded-lg border border-edge bg-night-3 px-2 py-1.5 text-sm text-mist"
          >
            {users.map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.name}
              </option>
            ))}
          </select>
        )}
      </aside>

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-20 border-b border-edge bg-night-2/95 backdrop-blur md:hidden">
        <div className="flex items-center gap-2 px-4 py-3">
          <Wordmark compact />
          {users.length > 1 && (
            <select
              value={userId}
              onChange={(e) => onUserChange(e.target.value)}
              className="ml-auto rounded border border-edge bg-night-3 px-1 py-0.5 text-xs text-mist"
            >
              {users.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ${
                  isActive ? "bg-grad-primary text-night" : "text-mist"
                }`
              }
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>

      <main className="min-w-0 flex-1 px-4 pb-10 pt-4 md:ml-56 md:px-8 md:pt-8">
        <Outlet />
      </main>
    </div>
  );
}
