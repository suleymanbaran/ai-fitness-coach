// Regenerates the README screenshots from the seeded demo profile.
// Usage: node scripts/shoot-demo.mjs [user_id]   (default u1; run `python -m scripts.seed --rich` first)
// CHROME=/path/to/binary overrides the browser. Prints a per-page content report so a blank
// or empty-state page is caught instead of being committed as a screenshot.
// NOTE: --disable-gpu is required on this machine; without it headless Chrome
// rasterizes some pages as blank white images.
import { chromium } from "playwright-core";
import { mkdirSync, writeFileSync } from "node:fs";

const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = "http://localhost:5173";
const OUT = "/tmp/coach-ui-shots";
const USER = process.argv[2] ?? "u1";
mkdirSync(OUT, { recursive: true });

const EMPTY = [
  "no data", "nothing logged", "no workouts", "no runs", "no meals",
  "no measurements", "no memories", "nothing here", "not found", "empty",
];

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--disable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error" && !m.text().includes("favicon")) errors.push(m.text());
});

await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.evaluate((u) => localStorage.setItem("coach_user", u), USER);

const routes = ["", "trends", "workouts", "cardio", "nutrition", "plan", "calendar", "measures", "memory", "cost"];
const report = [];

for (const r of routes) {
  const name = r || "today";
  await page.goto(`${BASE}/${r}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1800);
  const text = (await page.locator("body").innerText()).toLowerCase();
  const digits = (await page.locator("body").innerText()).match(/\d/g)?.length ?? 0;
  const hits = EMPTY.filter((e) => text.includes(e));
  await page.screenshot({ path: `${OUT}/${USER}-${name}.png`, fullPage: false });
  writeFileSync(`${OUT}/${USER}-${name}.txt`, await page.locator("body").innerText());
  // An SVG chart that actually rendered has path elements; an empty chart does not.
  const svgPaths = await page.locator("svg path").count();
  report.push({ page: name, chars: text.length, digits, svgPaths, empty: hits });
}

await browser.close();

for (const r of report) {
  const flag = r.empty.length ? ` EMPTY-HITS: ${r.empty.join(",")}` : "";
  console.log(
    `${r.page.padEnd(10)} chars=${String(r.chars).padStart(5)} digits=${String(r.digits).padStart(4)} ` +
    `svgPaths=${String(r.svgPaths).padStart(4)}${flag}`
  );
}
console.log(`console/page errors: ${errors.length}`);
for (const e of errors.slice(0, 5)) console.log("  !", e.slice(0, 200));
