// Run against the Vite dev server: node scripts/test-nutrition-template-portions.mjs
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--disable-gpu"],
});

try {
  const page = await browser.newPage();
  const template = {
    id: 1, name: "Portion test", meal: "breakfast", total_kcal: 200,
    items: [{ description: "100 g oats", servings: 0.5, kcal: 400, protein_g: 20, carbs_g: 60, fat_g: 8, fiber_g: null }],
  };
  let saved;
  await page.route("**/users**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/meal-templates") && request.method() === "PUT") {
      saved = request.postDataJSON();
      await route.fulfill({ json: template });
    } else {
      await route.fulfill({ json: path === "/users" ? [{ user_id: "u1", name: "Test", goal: "general_fitness" }] :
        path.endsWith("/meal-templates") ? [template] : [] });
    }
  });
  await page.goto(process.env.BASE_URL || "http://127.0.0.1:4175/nutrition");
  await page.getByRole("button", { name: "Portion test · 200 kcal" }).click();
  const addForm = page.locator("form").filter({ has: page.getByRole("button", { name: "Log meal" }) });
  assert.equal(await addForm.getByTitle("Grams", { exact: true }).inputValue(), "50");
  assert.equal(await addForm.getByTitle("kcal — editable").inputValue(), "200");
  assert.equal(await addForm.getByTitle("protein_g").inputValue(), "10");
  assert.equal(await addForm.getByTitle("carbs_g").inputValue(), "30");
  assert.equal(await addForm.getByTitle("fat_g").inputValue(), "4");
  assert.equal(await addForm.getByTitle("fiber_g").inputValue(), "");

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const editor = page.locator("form").filter({ has: page.getByRole("button", { name: "Update template" }) });
  assert.equal(await editor.getByTitle("Grams").inputValue(), "50");
  assert.equal(await editor.getByPlaceholder("P", { exact: true }).inputValue(), "10");
  const updated = page.waitForResponse((response) =>
    response.url().includes("/meal-templates") && response.request().method() === "PUT");
  await editor.getByRole("button", { name: "Update template" }).click();
  await updated;
  assert.deepEqual(saved.items[0], {
    description: "50 g oats", servings: 1, kcal: 200, protein_g: 10, carbs_g: 30, fat_g: 4, fiber_g: null,
  });
  console.log("Nutrition template portions passed.");
} finally {
  await browser.close();
}
