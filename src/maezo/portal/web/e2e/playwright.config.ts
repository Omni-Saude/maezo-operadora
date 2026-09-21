import { defineConfig } from "@playwright/test";

const artifacts = process.env.MAEZO_PORTAL_E2E_ARTIFACTS ?? "e2e-artifacts";

export default defineConfig({
  testDir: ".",
  // `*.pw.ts` so vitest (`npm run test`, PR-B plane) never scans the Playwright suite.
  testMatch: "*.pw.ts",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: `${artifacts}/playwright-report.json` }],
    ["html", { outputFolder: `${artifacts}/playwright-html`, open: "never" }],
  ],
  outputDir: `${artifacts}/test-results`,
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ignoreHTTPSErrors: true,
    // A machine with Google Chrome installed and no Playwright browser cache can run the
    // journey without the browser download (MAEZO_PORTAL_E2E_CHANNEL=chrome). CI leaves it
    // unset and installs the pinned chromium explicitly.
    channel: process.env.MAEZO_PORTAL_E2E_CHANNEL || undefined,
  },
});
