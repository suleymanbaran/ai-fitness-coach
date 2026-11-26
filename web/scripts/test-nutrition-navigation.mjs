// Run against the Vite dev server: node scripts/test-nutrition-navigation.mjs
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--disable-gpu"],
});

try {
  const page = await browser.newPage();
  await page.clock.setFixedTime(new Date(2026, 11, 31, 12));
  await page.route("**/users**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json: path === "/users" ? [{ user_id: "u1", name: "Test", goal: "general_fitness" }] : [],
    });
  });
  await page.goto(process.env.BASE_URL || "http://127.0.0.1:4175/nutrition");
  const dateInput = page.getByLabel("Selected nutrition day");
  const next = page.getByRole("button", { name: "Next day" });
  const previous = page.getByRole("button", { name: "Previous day" });

  assert.equal(await dateInput.inputValue(), "2026-12-31");
  assert.equal(await next.isEnabled(), true, "Next day must be enabled on today");
  await next.click();
  assert.equal(await dateInput.inputValue(), "2027-01-01");
  await next.click();
  assert.equal(await dateInput.inputValue(), "2027-01-02");
  await previous.click();
  assert.equal(await dateInput.inputValue(), "2027-01-01");
  console.log("Nutrition day navigation passed.");
} finally {
  await browser.close();
}
