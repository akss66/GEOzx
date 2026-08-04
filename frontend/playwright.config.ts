import { defineConfig, devices } from "@playwright/test";

const testPort = process.env.PLAYWRIGHT_PORT ?? "5173";
const baseURL = `http://127.0.0.1:${testPort}`;
const packageManager = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `${packageManager} dev --host 127.0.0.1 --port ${testPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
