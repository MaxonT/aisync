#!/usr/bin/env node
// aisync from npm: a thin launcher for the PyPI release (aisync-cli), which is the real code.
// It runs `uvx --from aisync-cli==<version> aisync ...`, or `pipx run` when only pipx is installed.
// Nothing is installed globally and no shell config is touched.
"use strict";
const { spawnSync } = require("child_process");
const { version } = require("../package.json");

const SPEC = `aisync-cli==${version}`;

function toolWorks(cmd) {
  const r = spawnSync(cmd, ["--version"], { stdio: "ignore" });
  return r.error === undefined && r.status === 0;
}

// Pure function: which command runs aisync. `works(cmd)` says whether a tool is on PATH.
function buildCommand(args, works = toolWorks) {
  if (works("uvx")) {
    return { cmd: "uvx", args: ["--from", SPEC, "aisync", ...args] };
  }
  if (works("pipx")) {
    return { cmd: "pipx", args: ["run", "--spec", SPEC, "aisync", ...args] };
  }
  return null;
}

function main(argv) {
  const plan = buildCommand(argv);
  if (plan === null) {
    process.stderr.write(
      "aisync (npm) needs uv or pipx to run the Python release.\n" +
        "  install uv:    https://docs.astral.sh/uv/   then run again\n" +
        "  or pipx:       pipx install aisync-cli      (then run `aisync` directly)\n"
    );
    return 127;
  }
  const r = spawnSync(plan.cmd, plan.args, { stdio: "inherit" });
  if (r.error) {
    process.stderr.write(`aisync (npm): could not start ${plan.cmd}: ${r.error.message}\n`);
    return 1;
  }
  return r.status === null ? 1 : r.status;
}

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}

module.exports = { buildCommand, SPEC };
