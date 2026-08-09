import { spawnSync } from "node:child_process";
import { basename, dirname, isAbsolute, resolve } from "node:path";

type CommandOptions = {
  cwd: string;
  encoding: "utf8";
  windowsHide: true;
};

type CommandResult = {
  error?: Error;
  status: number | null;
  stderr: string;
  stdout: string;
};

export type CommandRunner = (
  command: string,
  args: string[],
  options: CommandOptions,
) => CommandResult;

const runCommand: CommandRunner = (command, args, options) => {
  const result = spawnSync(command, args, options);
  return {
    error: result.error,
    status: result.status,
    stderr: result.stderr ?? "",
    stdout: result.stdout ?? "",
  };
};

function failureDetail(result: CommandResult): string {
  return result.error?.message || result.stderr.trim() || `exit status ${result.status}`;
}

export function resolveRequiredPythonExecutable(
  repositoryRoot: string,
  runner: CommandRunner = runCommand,
): string {
  const options: CommandOptions = {
    cwd: repositoryRoot,
    encoding: "utf8",
    windowsHide: true,
  };
  const gitProbe = runner(
    "git",
    ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    options,
  );
  const gitCommonDirOutput = gitProbe.stdout.trim();
  if (gitProbe.status !== 0 || !gitCommonDirOutput) {
    throw new Error(
      `Could not resolve the main checkout from ${repositoryRoot}: ${failureDetail(gitProbe)}`,
    );
  }

  const gitCommonDir = isAbsolute(gitCommonDirOutput)
    ? resolve(gitCommonDirOutput)
    : resolve(repositoryRoot, gitCommonDirOutput);
  if (basename(gitCommonDir).toLowerCase() !== ".git") {
    throw new Error(`Unexpected Git common directory: ${gitCommonDir}`);
  }

  const pythonExecutable = resolve(
    dirname(gitCommonDir),
    "venv",
    "Scripts",
    "python.exe",
  );
  const pythonProbe = runner(
    pythonExecutable,
    ["-c", "import uvicorn"],
    options,
  );
  if (pythonProbe.status !== 0) {
    throw new Error(
      `Required project Python is not runnable: ${pythonExecutable}: ${failureDetail(pythonProbe)}`,
    );
  }
  return pythonExecutable;
}

export function withoutPythonOverrides(
  environment: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  return Object.fromEntries(
    Object.entries(environment).filter(
      ([key]) => !["E2E_PYTHON", "PYTHONPATH"].includes(key.toUpperCase()),
    ),
  );
}
