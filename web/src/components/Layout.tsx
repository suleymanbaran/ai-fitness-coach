import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  Brain,
  Coins,
  Dumbbell,
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
  { to: "/nutrition", label: "Nutrition", icon: Utensils },
  { to: "/workouts", label: "Workouts", icon: Dumbbell },
  { to: "/measures", label: "Measures", icon: Ruler },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/cost", label: "Cost", icon: Coins },
  { to: "/settings", label: "Settings", icon: Settings },
];

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
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-56 flex-col border-r border-zinc-800 bg-zinc-950 p-4 md:flex">
        <div className="mb-8 flex items-center gap-2 px-2">
          <Activity className="h-6 w-6 text-lime-400" />
          <span className="text-lg font-bold tracking-tight">Fitness Coach</span>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "bg-lime-400/10 text-lime-400" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        {users.length > 1 && (
          <select
            value={userId}
            onChange={(e) => onUserChange(e.target.value)}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-300"
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
      <div className="fixed inset-x-0 top-0 z-20 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur md:hidden">
        <div className="flex items-center gap-2 px-4 py-3">
          <Activity className="h-5 w-5 text-lime-400" />
          <span className="font-bold">Fitness Coach</span>
          {users.length > 1 && (
            <select
              value={userId}
              onChange={(e) => onUserChange(e.target.value)}
              className="ml-auto rounded border border-zinc-800 bg-zinc-900 px-1 py-0.5 text-xs text-zinc-300"
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
                `flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${
                  isActive ? "bg-lime-400/10 text-lime-400" : "text-zinc-500"
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
