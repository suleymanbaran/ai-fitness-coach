// Headless verification of the React dashboard against the live API.
// Usage: node scripts/verify-ui.mjs
// NOTE: --disable-gpu is required on this machine; without it headless Chrome
// rasterizes some pages as blank white images.
import { chromium } from "playwright-core";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = "http://localhost:5173";
const SHOTS = "/tmp/coach-ui-shots";

import { mkdirSync } from "node:fs";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--disable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error" && !msg.text().includes("favicon")) consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push(String(err)));

const results = [];

async function check(name, path, expectations) {
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1200); // let recharts animate in
  const body = (await page.locator("body").innerText()).toLowerCase();
  const found = {};
  for (const needle of expectations) {
    // Card titles are CSS-uppercased, so compare case-insensitively.
    found[needle] = body.includes(needle.toLowerCase());
  }
  const shot = `${SHOTS}/${name}.png`;
  await page.screenshot({ path: shot, fullPage: false });
  results.push({ page: name, path, found, shot });
}

await check("today", "/", ["Today", "Energy", "Total burn", "Today's session", "HRV"]);
await check("workouts", "/workouts", ["Strength progression", "Bench press", "Top weight", "Log a workout"]);
await check("trends", "/trends", ["Energy balance", "Weight & body fat", "HRV & resting heart rate", "Calorie intake"]);
await check("plan", "/plan", ["Weekly plan", "Planning happens on Telegram", "Revise it"]);
await check("nutrition", "/nutrition", ["Meals", "Add meal", "Compute nutrition", "Intake (14 days)", "Nutrition plan"]);
await check("cardio", "/cardio", ["Cardio capacity", "Pace and heart rate", "Log a run", "Run history"]);
await check("calendar", "/calendar", ["Calendar", "workout or run logged", "Steps", "Nutrition (taken / target)"]);
await check("measures", "/measures", ["Body measures", "Add measurement", "History"]);
await check("memory", "/memory", ["Coach memory", "Add memory"]);
await check("cost", "/cost", ["Cache hit rate", "gpt-6-luna", "By model and purpose"]);

await browser.close();

let ok = true;
for (const r of results) {
  const missing = Object.entries(r.found).filter(([, v]) => !v).map(([k]) => k);
  const status = missing.length === 0 ? "PASS" : `MISSING: ${missing.join(", ")}`;
  if (missing.length) ok = false;
  console.log(`${r.page.padEnd(10)} ${status}`);
}
console.log(`console errors: ${consoleErrors.length}`);
for (const e of consoleErrors.slice(0, 5)) console.log("  !", e.slice(0, 160));
process.exit(ok && consoleErrors.length === 0 ? 0 : 1);
