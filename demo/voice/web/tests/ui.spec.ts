import { expect, test } from "@playwright/test";

// These exist because three UI bugs shipped that could not be seen from a terminal: a dialog that never
// opened, a mobile-silent reply, and a speech gate that only fired for a shout. A browser catches the
// first class of those in two seconds.
const APP = process.env.APP_URL ?? "http://localhost:8085";

test("the page renders its controls", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(APP);
  await expect(page.getByRole("button", { name: /start conversation/i })).toBeVisible();
  // The model picker only exists when there is a choice to make: the list comes from the engine, and it
  // is down to one model at the moment.
  const offered = (await (await page.request.get(`${APP}/api/models`)).json()).models.length;
  await expect(page.getByRole("combobox", { name: /chat model/i })).toHaveCount(offered > 1 ? 1 : 0);
  await expect(page.getByRole("combobox", { name: /sensitivity/i })).toBeVisible();
  expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
});

test("the voice selector opens, searches and selects", async ({ page }) => {
  await page.goto(APP);
  const trigger = page.getByRole("button", { name: /^Voice:/ });
  await expect(trigger).toBeVisible();
  const before = await trigger.textContent();

  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();                       // this is what was broken
  await expect(dialog.getByPlaceholder(/search/i)).toBeVisible();
  await expect(dialog.getByRole("option")).not.toHaveCount(0);

  await dialog.getByPlaceholder(/search/i).fill("george");
  const option = dialog.getByRole("option").first();
  await expect(option).toContainText(/george/i);
  await option.click();

  await expect(dialog).toBeHidden();
  await expect(trigger).not.toHaveText(before ?? "");       // the choice actually took
});

test("the microphone selector opens", async ({ page }) => {
  await page.goto(APP);
  await page.getByRole("button", { name: /select microphone|microphone/i }).first().click();
  await expect(page.getByPlaceholder(/search microphones/i)).toBeVisible();
});

test("a typed message is answered without touching the microphone", async ({ page }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${APP}${process.env.APP_PASS ? `/?pass=${process.env.APP_PASS}` : ""}`);
  const box = page.getByRole("textbox", { name: /type a message/i });
  await box.fill("What is the capital of France? Answer in one short sentence.");
  await page.getByRole("button", { name: /^send$/i }).click();
  await expect(box).toHaveValue("");                                  // cleared on send
  await expect(page.getByText(/capital of France/i).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/paris/i).first()).toBeVisible({ timeout: 40_000 });
  // The conversation pane scrolls rather than growing the page.
  const pane = page.locator("section[aria-label='Conversation'] div").first();
  expect(await pane.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
  expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
});
