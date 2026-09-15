import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  workers: 1, // the conversation tests share one GPU; running them side by side distorts every latency they measure
  use: { headless: true, launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] } },
  reporter: [["list"]],
});
