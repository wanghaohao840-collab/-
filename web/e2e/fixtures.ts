import {
  spawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";

import { expect, test as base, type Page } from "@playwright/test";

import {
  resolveRequiredPythonExecutable,
  withoutPythonOverrides,
} from "./python-runtime";

type WorkerFixtures = {
  appUrl: string;
};

const repositoryRoot = resolve(import.meta.dirname, "../..");
const pythonExecutable = resolveRequiredPythonExecutable(repositoryRoot);
const frontendIndex = resolve(repositoryRoot, "web/dist/index.html");

async function reservePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate an E2E server port"));
        return;
      }
      const port = address.port;
      server.close((error) => (error ? reject(error) : resolvePort(port)));
    });
  });
}

async function waitForServer(
  process: ChildProcessWithoutNullStreams,
  url: string,
  logs: string[],
): Promise<void> {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    if (process.exitCode !== null) {
      throw new Error(`Uvicorn exited before readiness:\n${logs.join("").slice(-4_000)}`);
    }
    try {
      const response = await fetch(`${url}/healthz`);
      if (response.ok) {
        return;
      }
    } catch {
      // The listener is not ready yet.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error(`Timed out waiting for Uvicorn:\n${logs.join("").slice(-4_000)}`);
}

async function stopProcess(process: ChildProcessWithoutNullStreams): Promise<void> {
  if (process.exitCode !== null) {
    return;
  }
  const exited = new Promise<void>((resolveExit) => process.once("exit", () => resolveExit()));
  process.kill();
  await Promise.race([
    exited,
    new Promise<void>((resolveTimeout) => setTimeout(resolveTimeout, 5_000)),
  ]);
  if (process.exitCode === null) {
    process.kill("SIGKILL");
    await exited;
  }
}

export const test = base.extend<object, WorkerFixtures>({
  appUrl: [
    async ({ browserName }, use) => {
      if (browserName !== "chromium") {
        throw new Error(`Task 6 supports exactly one browser: received ${browserName}`);
      }
      if (!existsSync(frontendIndex)) {
        throw new Error("Build web/dist before running Playwright: npm run build");
      }

      const expectedParent = resolve(repositoryRoot, ".runtime");
      mkdirSync(expectedParent, { recursive: true });
      const dataRoot = mkdtempSync(join(expectedParent, "zhiyan-playwright-"));
      if (dirname(resolve(dataRoot)) !== expectedParent) {
        throw new Error(`Unexpected E2E data root: ${dataRoot}`);
      }
      const port = await reservePort();
      const appUrl = `http://127.0.0.1:${port}`;
      const logs: string[] = [];
      const process = spawn(
        pythonExecutable,
        [
          "-m",
          "uvicorn",
          "server:app",
          "--host",
          "127.0.0.1",
          "--port",
          String(port),
        ],
        {
          cwd: repositoryRoot,
          env: {
            ...withoutPythonOverrides(globalThis.process.env),
            PDF_ASSISTANT_DATA_DIR: dataRoot,
            PYTHONUNBUFFERED: "1",
          },
        },
      );
      process.stdout.on("data", (chunk: Buffer) => logs.push(chunk.toString()));
      process.stderr.on("data", (chunk: Buffer) => logs.push(chunk.toString()));

      try {
        await waitForServer(process, appUrl, logs);
        await use(appUrl);
      } finally {
        await stopProcess(process);
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
        rmSync(dataRoot, {
          force: true,
          maxRetries: 10,
          recursive: true,
          retryDelay: 250,
        });
      }
    },
    { scope: "worker", timeout: 60_000 },
  ],
});

export { expect };

export function uniqueUsername(prefix: string): string {
  return `${prefix}_${randomUUID().replaceAll("-", "").slice(0, 12)}`;
}

export async function registerUser(
  page: Page,
  appUrl: string,
  username: string,
): Promise<void> {
  await page.goto(`${appUrl}/register`);
  await expect(page.getByRole("heading", { level: 1, name: "注册" })).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("e2e-only-passphrase");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page).toHaveURL(`${appUrl}/overview`, { timeout: 30_000 });
  await expect(page.getByRole("heading", { level: 1, name: "学习概览" })).toBeVisible();
}

export async function openMore(page: Page, projectName: string) {
  const name = projectName === "mobile" ? "更多" : "更多操作";
  const trigger = page.getByRole("button", { name, exact: true });
  await trigger.click();
  await expect(page.getByRole("dialog", { name: "更多" })).toBeVisible();
  return trigger;
}

export async function settleVisuals(page: Page): Promise<void> {
  await page.addStyleTag({
    content:
      "*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }",
  });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolveFrame) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolveFrame())),
    );
  });
}
