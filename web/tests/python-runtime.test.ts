import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  resolveRequiredPythonExecutable,
  withoutPythonOverrides,
  type CommandRunner,
} from "../e2e/python-runtime";

type CommandCall = {
  args: string[];
  command: string;
};

describe("Playwright Python runtime selection", () => {
  it("probes only the main checkout project venv resolved from the Git common directory", () => {
    const mainCheckout = resolve("D:/python_self_agent");
    const worktree = resolve(
      mainCheckout,
      ".worktrees/figma-product-ui-foundation",
    );
    const expectedPython = resolve(mainCheckout, "venv/Scripts/python.exe");
    const calls: CommandCall[] = [];
    const runner: CommandRunner = (command, args) => {
      calls.push({ command, args });
      return calls.length === 1
        ? {
            status: 0,
            stderr: "",
            stdout: `${resolve(mainCheckout, ".git")}\n`,
          }
        : { status: 0, stderr: "", stdout: "" };
    };

    expect(resolveRequiredPythonExecutable(worktree, runner)).toBe(
      expectedPython,
    );
    expect(calls).toEqual([
      {
        command: "git",
        args: ["rev-parse", "--path-format=absolute", "--git-common-dir"],
      },
      {
        command: expectedPython,
        args: ["-c", "import uvicorn"],
      },
    ]);
  });

  it("fails before probing Python when the main checkout cannot be resolved", () => {
    const calls: CommandCall[] = [];
    const runner: CommandRunner = (command, args) => {
      calls.push({ command, args });
      return { status: 128, stderr: "not a repository", stdout: "" };
    };

    expect(() =>
      resolveRequiredPythonExecutable("D:/broken-worktree", runner),
    ).toThrow("Could not resolve the main checkout");
    expect(calls).toHaveLength(1);
  });

  it("fails immediately when the one mandated interpreter is not runnable", () => {
    const mainCheckout = resolve("D:/python_self_agent");
    const expectedPython = resolve(mainCheckout, "venv/Scripts/python.exe");
    const calls: CommandCall[] = [];
    const runner: CommandRunner = (command, args) => {
      calls.push({ command, args });
      return calls.length === 1
        ? {
            status: 0,
            stderr: "",
            stdout: `${resolve(mainCheckout, ".git")}\n`,
          }
        : { status: 1, stderr: "launcher is broken", stdout: "" };
    };

    expect(() =>
      resolveRequiredPythonExecutable(
        resolve(mainCheckout, ".worktrees/figma-product-ui-foundation"),
        runner,
      ),
    ).toThrow(`Required project Python is not runnable: ${expectedPython}`);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.command).toBe(expectedPython);
  });

  it("removes interpreter and import-path overrides from the server environment", () => {
    expect(
      withoutPythonOverrides({
        E2E_PYTHON: "D:/Anaconda/python.exe",
        Path: "C:/Windows/System32",
        PythonPath: "D:/recovery/site-packages",
        SAFE_VALUE: "kept",
      }),
    ).toEqual({
      Path: "C:/Windows/System32",
      SAFE_VALUE: "kept",
      PYTHON_DOTENV_DISABLED: "1",
    });
  });

  it("isolates service credentials case-insensitively without mutating the parent", () => {
    const parent = Object.freeze({
      Path: "C:/Windows/System32", TEMP: "C:/test-temp",
      LLM_API_KEY: "test-only", OpenAI_API_KEY: "test-only",
      DEEPSEEK_API_KEY: "test-only", NEO4J_URI: "test-only",
      NEO4J_TEST_PASSWORD: "test-only", QDRANT_API_KEY: "test-only",
      rag_backend: "qdrant", RAG_EMBEDDING_PROVIDER: "siliconflow",
      python_dotenv_disabled: "0", PYTHON_DOTENV_DISABLED: "0",
    });
    expect(withoutPythonOverrides(parent)).toEqual({ Path: parent.Path, TEMP: parent.TEMP, PYTHON_DOTENV_DISABLED: "1" });
    expect(parent.LLM_API_KEY).toBe("test-only");
    expect(parent.PYTHON_DOTENV_DISABLED).toBe("0");
  });
});
