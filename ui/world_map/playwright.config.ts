import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180_000,
  use: {
    baseURL: "http://127.0.0.1:8001",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "cd ../.. && PYTHONPATH=src .venv/bin/python -m uvicorn isite2.api.main:app --host 127.0.0.1 --port 8001",
    url: "http://127.0.0.1:8001/health",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
