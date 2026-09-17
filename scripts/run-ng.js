"use strict";

/**
 * Run Angular CLI with cwd = frontend/ (single app, no nested folder).
 * Usage (repo root): node scripts/run-ng.js serve
 */
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const root = path.resolve(__dirname, "..");
const appDir = path.join(root, "frontend");
const ngBin = path.join(appDir, "node_modules", "@angular", "cli", "bin", "ng");
if (!fs.existsSync(ngBin)) {
  console.error("Angular CLI not found. Run: cd frontend && npm install");
  process.exit(1);
}

const nodeOpts = String(process.env.NODE_OPTIONS || "").trim();
const need = ["--openssl-legacy-provider", "--no-deprecation"];
process.env.NODE_OPTIONS = need
  .filter((f) => !nodeOpts.includes(f))
  .concat(nodeOpts ? [nodeOpts] : [])
  .join(" ")
  .trim();

const args = process.argv.slice(2);
const child = spawn(process.execPath, [ngBin, ...args], {
  cwd: appDir,
  stdio: "inherit",
  env: process.env,
  shell: false,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code === null ? 1 : code);
});
