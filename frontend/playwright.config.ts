import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4173/classroom-assistant/",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command:
        "npm run preview -- --mode pages --port 4173 --base /classroom-assistant/",
      url: "http://127.0.0.1:4173/classroom-assistant/",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "node node_modules/vite/bin/vite.js preview --port 4174",
      url: "http://127.0.0.1:4174",
      reuseExistingServer: !process.env.CI,
    },
  ],
});
