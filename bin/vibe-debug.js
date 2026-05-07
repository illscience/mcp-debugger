#!/usr/bin/env node
"use strict";

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const packageRoot = path.resolve(__dirname, "..");
const packageJson = require(path.join(packageRoot, "package.json"));

function cacheRoot() {
  if (process.env.VIBE_DEBUG_CACHE) {
    return process.env.VIBE_DEBUG_CACHE;
  }
  if (process.env.XDG_CACHE_HOME) {
    return path.join(process.env.XDG_CACHE_HOME, "vibe-debug");
  }
  return path.join(os.homedir(), ".cache", "vibe-debug");
}

function run(command, args, options = {}) {
  const result = childProcess.spawnSync(command, args, {
    stdio: options.stdio || "ignore",
    env: process.env,
  });
  return result.status === 0;
}

function runSetup(command, args, failureMessage) {
  const result = childProcess.spawnSync(command, args, {
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
    encoding: "utf8",
  });

  if (process.env.VIBE_DEBUG_VERBOSE_INSTALL) {
    if (result.stdout) {
      process.stderr.write(result.stdout);
    }
    if (result.stderr) {
      process.stderr.write(result.stderr);
    }
  }

  if (result.status !== 0) {
    if (result.stdout) {
      process.stderr.write(result.stdout);
    }
    if (result.stderr) {
      process.stderr.write(result.stderr);
    }
    console.error(failureMessage);
    process.exit(result.status || 1);
  }
}

function pythonCandidates() {
  const configuredPython = process.env.VIBE_DEBUG_PYTHON;
  if (configuredPython) {
    return [{ command: configuredPython, args: [] }];
  }
  if (process.platform === "win32") {
    return [
      { command: "py", args: ["-3"] },
      { command: "python", args: [] },
      { command: "python3", args: [] },
    ];
  }
  return [
    { command: "python3", args: [] },
    { command: "python", args: [] },
  ];
}

function findPython() {
  for (const candidate of pythonCandidates()) {
    if (
      run(candidate.command, [
        ...candidate.args,
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
      ])
    ) {
      return candidate;
    }
  }

  console.error("vibe-debug requires Python 3.10 or newer.");
  console.error("Install Python, or set VIBE_DEBUG_PYTHON to a Python executable.");
  process.exit(1);
}

function venvPython(venvDir) {
  if (process.platform === "win32") {
    return path.join(venvDir, "Scripts", "python.exe");
  }
  return path.join(venvDir, "bin", "python");
}

function isReady(readyFile, pythonPath, sourceDir) {
  return fs.existsSync(readyFile) && fs.existsSync(pythonPath) && fs.existsSync(sourcePath(sourceDir));
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function acquireInstallLock(lockDir) {
  const startedAt = Date.now();
  const staleAfterMs = 5 * 60 * 1000;
  const timeoutMs = 2 * 60 * 1000;

  fs.mkdirSync(path.dirname(lockDir), { recursive: true });

  while (true) {
    try {
      fs.mkdirSync(lockDir);
      return () => fs.rmSync(lockDir, { recursive: true, force: true });
    } catch (error) {
      if (!error || error.code !== "EEXIST") {
        throw error;
      }

      try {
        const stat = fs.statSync(lockDir);
        if (Date.now() - stat.mtimeMs > staleAfterMs) {
          fs.rmSync(lockDir, { recursive: true, force: true });
          continue;
        }
      } catch {
        continue;
      }

      if (Date.now() - startedAt > timeoutMs) {
        console.error(`Timed out waiting for the vibe-debug install lock: ${lockDir}`);
        process.exit(1);
      }
      sleep(100);
    }
  }
}

function sourcePath(sourceDir) {
  return path.join(sourceDir, "src");
}

function hashPath(hash, target, root) {
  if (!fs.existsSync(target)) {
    return;
  }
  const relative = path.relative(root, target);
  const stat = fs.statSync(target);
  if (stat.isDirectory()) {
    hash.update(`dir:${relative}\n`);
    for (const entry of fs.readdirSync(target).sort()) {
      const child = path.join(target, entry);
      if (shouldCopySource(child)) {
        hashPath(hash, child, root);
      }
    }
    return;
  }
  hash.update(`file:${relative}:${stat.size}\n`);
  hash.update(fs.readFileSync(target));
}

function sourceFingerprint() {
  const hash = crypto.createHash("sha256");
  hash.update(`${packageJson.name}@${packageJson.version}\n`);
  for (const name of [
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "MANIFEST.in",
    "AGENTS.md",
    "CLAUDE.md",
    "src",
    "examples",
    "tools",
  ]) {
    hashPath(hash, path.join(packageRoot, name), packageRoot);
  }
  return hash.digest("hex").slice(0, 12);
}

function shouldCopySource(source) {
  const name = path.basename(source);
  if (
    name === ".git" ||
    name === ".github" ||
    name === ".venv" ||
    name === "__pycache__" ||
    name === "build" ||
    name === "dist" ||
    name === "node_modules"
  ) {
    return false;
  }
  if (name.endsWith(".egg-info") || name.endsWith(".pyc") || name.endsWith(".pyo")) {
    return false;
  }
  return true;
}

function copyIfPresent(name, sourceDir) {
  const source = path.join(packageRoot, name);
  if (!fs.existsSync(source)) {
    return;
  }
  fs.cpSync(source, path.join(sourceDir, name), {
    recursive: true,
    filter: shouldCopySource,
  });
}

function prepareInstallSource(sourceDir) {
  fs.rmSync(sourceDir, { recursive: true, force: true });
  fs.mkdirSync(sourceDir, { recursive: true });

  for (const name of [
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "MANIFEST.in",
    "AGENTS.md",
    "CLAUDE.md",
    "src",
    "examples",
    "tools",
  ]) {
    copyIfPresent(name, sourceDir);
  }
}

function ensureVenv() {
  const safeName = packageJson.name.replace(/[^a-zA-Z0-9._-]/g, "_");
  const fingerprint = sourceFingerprint();
  const cacheKey = `${safeName}-${packageJson.version}-${fingerprint}`;
  const venvDir = path.join(cacheRoot(), cacheKey);
  const sourceDir = path.join(venvDir, "package");
  const readyFile = path.join(venvDir, ".ready");
  const pythonPath = venvPython(venvDir);

  if (isReady(readyFile, pythonPath, sourceDir)) {
    return { pythonPath, sourceDir };
  }

  const releaseLock = acquireInstallLock(`${venvDir}.lock`);
  try {
    if (isReady(readyFile, pythonPath, sourceDir)) {
      return { pythonPath, sourceDir };
    }

    fs.rmSync(venvDir, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(venvDir), { recursive: true });

    const python = findPython();
    runSetup(
      python.command,
      [...python.args, "-m", "venv", venvDir],
      "Failed to create the vibe-debug Python environment.",
    );

    prepareInstallSource(sourceDir);
    runSetup(
      pythonPath,
      ["-m", "pip", "--disable-pip-version-check", "install", "debugpy>=1.8.0"],
      "Failed to install the Python debugger backend.",
    );

    fs.writeFileSync(readyFile, `${cacheKey}\n`, "utf8");
    return { pythonPath, sourceDir };
  } finally {
    releaseLock();
  }
}

function main() {
  const args = process.argv.slice(2);
  const { pythonPath, sourceDir } = ensureVenv();
  const pythonModulePath = sourcePath(sourceDir);

  let moduleName = "vibe_debug.mcp_server";
  let moduleArgs = args;

  if (args.length > 0) {
    if (args[0] === "server" || args[0] === "mcp-server") {
      moduleArgs = args.slice(1);
    } else {
      moduleName = "vibe_debug.cli";
    }
  }

  const env = {
    ...process.env,
    PYTHONPATH: [pythonModulePath, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    VIBE_DEBUG_SERVER_COMMAND_JSON: JSON.stringify([pythonPath, "-m", "vibe_debug.mcp_server"]),
  };

  const child = childProcess.spawn(pythonPath, ["-m", moduleName, ...moduleArgs], {
    stdio: "inherit",
    env,
  });

  child.on("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
    } else {
      process.exit(code || 0);
    }
  });
}

main();
