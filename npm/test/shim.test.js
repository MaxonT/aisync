// Run: node --test npm/test/shim.test.js   (Node 18+, no dependencies)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const { buildCommand, SPEC } = require("../bin/aisync.js");

const ROOT = path.resolve(__dirname, "..", "..");
const BIN = path.resolve(__dirname, "..", "bin", "aisync.js");
const pkg = require("../package.json");

test("the npm version is the PyPI version (pyproject.toml)", () => {
  const toml = fs.readFileSync(path.join(ROOT, "pyproject.toml"), "utf8");
  const m = toml.match(/^version = "([^"]+)"/m);
  assert.ok(m, "pyproject version found");
  assert.equal(pkg.version, m[1]);
  assert.equal(SPEC, `aisync-cli==${m[1]}`);
});

test("prefers uvx, then pipx, pinned to the same release", () => {
  const both = buildCommand(["--version"], (c) => c === "uvx" || c === "pipx");
  assert.equal(both.cmd, "uvx");
  assert.deepEqual(both.args, ["--from", SPEC, "aisync", "--version"]);

  const onlyPipx = buildCommand(["doctor"], (c) => c === "pipx");
  assert.equal(onlyPipx.cmd, "pipx");
  assert.deepEqual(onlyPipx.args, ["run", "--spec", SPEC, "aisync", "doctor"]);
});

test("no uv and no pipx gives null", () => {
  assert.equal(buildCommand(["--version"], () => false), null);
});

test("without uv or pipx the launcher explains and exits 127", () => {
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), "aisync-npm-empty-"));
  const r = spawnSync(process.execPath, [BIN, "--version"], { env: { PATH: empty }, encoding: "utf8" });
  assert.equal(r.status, 127);
  assert.match(r.stderr, /needs uv or pipx/);
});

test("arguments and exit code pass through a runner", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aisync-npm-fake-"));
  const fake = path.join(dir, "uvx");
  // the launcher probes `uvx --version` first, so the fake answers that with success
  fs.writeFileSync(fake, '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "uvx 0.0"; exit 0; fi\necho "ARGS:$*"\nexit 3\n');
  fs.chmodSync(fake, 0o755);
  const r = spawnSync(process.execPath, [BIN, "log", "--summary", "hi there"], {
    env: { PATH: dir }, encoding: "utf8",
  });
  assert.equal(r.status, 3);
  assert.match(r.stdout, new RegExp(`ARGS:--from ${SPEC.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} aisync log --summary hi there`));
});

// Does PyPI already have this exact release? The end-to-end check needs it; before the release it is skipped
// (the release workflow runs this suite before it publishes, so it must not fail on the version it is about to ship).
function onPyPI(version) {
  const r = spawnSync("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}",
    `https://pypi.org/pypi/aisync-cli/${version}/json`], { encoding: "utf8" });
  return r.status === 0 && r.stdout.trim() === "200";
}

test("end to end: the real uvx fetches the pinned PyPI release", { timeout: 180000 }, (t) => {
  const probe = spawnSync("uvx", ["--version"], { encoding: "utf8" });
  if (probe.error || probe.status !== 0) {
    t.skip("uvx not installed");
    return;
  }
  if (!onPyPI(pkg.version)) {
    t.skip(`aisync-cli ${pkg.version} is not on PyPI yet`);
    return;
  }
  const r = spawnSync(process.execPath, [BIN, "--version"], { encoding: "utf8", timeout: 170000 });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), `aisync ${pkg.version}`);
});
