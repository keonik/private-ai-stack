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
  if (process.env.DEBUG_GATE) page.on("console", (m) => m.text().startsWith("[gate]") && console.log(`${(performance.now() / 1000).toFixed(2)} ${m.text()}`));
  await page.addInitScript(
    ([l, e]) => {
      localStorage.setItem("lab", JSON.stringify(l));
      localStorage.setItem("engine", e as string);
      localStorage.setItem("debug", "1");
    },
    [lab, engine] as const
  );
  await page.goto(PASS ? `${APP}/?pass=${PASS}` : APP);
  return { browser, page };
}

const rows = (page: Page) => page.locator("section[aria-label='Conversation lab'] tbody tr");

test("a spoken question is answered, streamed, and timed", async () => {
  test.setTimeout(60_000);
  const { browser, page } = await open("turn", { reply: "eager", barge: "instant", fillers: "off", endOfTurn: 700, turn: "pause", transcript: "written" });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(page.getByText(/sky blue/i).first()).toBeVisible({ timeout: 15_000 });
    await expect(rows(page).first()).toContainText("done", { timeout: 40_000 });
    const cells = await rows(page).first().locator("td").allTextContents();
    console.log("turn row:", cells.join(" | "));
    const voiceAt = Number(cells[6]);
    expect(voiceAt).toBeGreaterThan(0);
    expect(voiceAt).toBeLessThan(4);
  } finally {
    await browser.close();
  }
});

test("talking over the reply stops it and answers the new question", async () => {
  test.setTimeout(90_000);
  const { browser, page } = await open("barge", { reply: "eager", barge: "smart", fillers: "slow", endOfTurn: 700, turn: "smart", transcript: "spoken" });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    // Generous: a long story plus a cold model can push a real interruption past 20 s.
    await expect(page.getByText(/interrupted|continued/).first()).toBeVisible({ timeout: 35_000 });
    await expect(page.getByText(/paris/i).first()).toBeVisible({ timeout: 35_000 });
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
  const { browser, page } = await open("turn", { reply: "eager", barge: "smart", fillers: "slow", endOfTurn: 700, turn: "smart", transcript: "spoken" }, "qwen3-tts-1.7b");
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(rows(page).first()).toContainText("done", { timeout: 45_000 });
    const cells = await rows(page).first().locator("td").allTextContents();
    console.log("qwen3-tts row:", cells.join(" | "));
    expect(cells[1]).toContain("qwen3");
    const firstHeard = Math.min(Number(cells[5]) || 99, Number(cells[6]) || 99);
    expect(firstHeard).toBeLessThan(2.5);
  } finally {
    await browser.close();
  }
});

test("Smart Turn: stopping mid-thought and carrying on is answered as one question", async () => {
  test.setTimeout(60_000);
  const { browser, page } = await open("reopen", { reply: "eager", barge: "smart", fillers: "off", endOfTurn: 700, turn: "smart", transcript: "spoken" });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(rows(page).first()).toContainText("done", { timeout: 40_000 });
    const all = await rows(page).evaluateAll((trs) => trs.map((tr) => tr.textContent));
    console.log("reopen rows:", all);
    const you = await page.locator("article").filter({ hasText: /^You/ }).allTextContents();
    console.log("you said:", you);
    // One answered turn, and it contains both halves of the sentence.
    expect(you).toHaveLength(1);
    expect(you[0]).toMatch(/wondering/i);
    expect(you[0]).toMatch(/mountain/i);
  } finally {
    await browser.close();
  }
});

test("smart interrupting: a 'yeah' while it talks does not stop the story", async () => {
  test.setTimeout(75_000);
  const { browser, page } = await open("backchannel", { reply: "eager", barge: "smart", fillers: "off", endOfTurn: 700, turn: "smart", transcript: "spoken" });
  try {
    await page.getByRole("button", { name: /start conversation/i }).click();
    await expect(page.locator("section[aria-label='Conversation lab'] tbody")).toContainText("ignored", { timeout: 30_000 });
    await expect(rows(page).last()).toContainText("done", { timeout: 45_000 });
    const all = await rows(page).evaluateAll((trs) => trs.map((tr) => tr.textContent));
    console.log("backchannel rows:", all);
    expect(all.join(" ")).not.toContain("interrupted");
  } finally {
    await browser.close();
  }
});
