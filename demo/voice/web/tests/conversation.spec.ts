import { chromium, expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// A whole conversation in a real browser, against the real models: Chromium's fake microphone plays a
// recorded voice into the page, so the speech gate, the streamed reply, the playback queue and talking over
// the reply all run exactly as they do for a person. There is no room echo here, so this proves the logic
// of interrupting, not how it behaves on a laptop speaker.
//
//   APP_URL=http://localhost:8085 APP_PASS=<a tester pass> npx playwright test conversation
const APP = process.env.APP_URL ?? "http://localhost:8085";
const PASS = process.env.APP_PASS ?? "";

async function open(fixture: string, lab: Record<string, unknown>, engine = "kokoro-tts") {
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${path.join(HERE, "fixtures", `${fixture}.wav`)}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const page = await browser.newPage();
  await page.addInitScript(
    ([l, e]) => {
      localStorage.setItem("lab", JSON.stringify(l));
      localStorage.setItem("engine", e as string);
    },
    [lab, engine] as const
  );
  await page.goto(PASS ? `${APP}/?pass=${PASS}` : APP);
  return { browser, page };
}

const rows = (page: Page) => page.locator("section[aria-label='Conversation lab'] tbody tr");

test("a spoken question is answered, streamed, and timed", async () => {
  test.setTimeout(60_000);
  const { browser, page } = await open("turn", { reply: "eager", interrupt: true, fillers: "off", endOfTurn: 700 });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(page.getByText(/sky blue/i).first()).toBeVisible({ timeout: 15_000 });
    await expect(rows(page).first()).toContainText("done", { timeout: 40_000 });
    const cells = await rows(page).first().locator("td").allTextContents();
    console.log("turn row:", cells.join(" | "));
    const voiceAt = Number(cells[5]);
    expect(voiceAt).toBeGreaterThan(0);
    expect(voiceAt).toBeLessThan(4);
  } finally {
    await browser.close();
  }
});

test("talking over the reply stops it and answers the new question", async () => {
  test.setTimeout(45_000);
  const { browser, page } = await open("barge", { reply: "eager", interrupt: true, fillers: "slow", endOfTurn: 700 });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(page.getByText(/interrupted|continued/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/paris/i).first()).toBeVisible({ timeout: 20_000 });
    const all = await rows(page).evaluateAll((trs) => trs.map((tr) => tr.textContent));
    console.log("barge rows:", all);
    const turns = await page.locator("article").allTextContents();
    console.log("transcript:", turns);
  } finally {
    await browser.close();
  }
});

test("the slowest realistic engine fills the wait instead of leaving silence", async () => {
  test.setTimeout(60_000);
  const { browser, page } = await open("turn", { reply: "eager", interrupt: true, fillers: "slow", endOfTurn: 700 }, "qwen3-tts-1.7b");
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(rows(page).first()).toContainText("done", { timeout: 45_000 });
    const cells = await rows(page).first().locator("td").allTextContents();
    console.log("qwen3-tts row:", cells.join(" | "));
    expect(cells[1]).toContain("qwen3");
    const firstHeard = Math.min(Number(cells[4]) || 99, Number(cells[5]) || 99);
    expect(firstHeard).toBeLessThan(2.5);
  } finally {
    await browser.close();
  }
});
