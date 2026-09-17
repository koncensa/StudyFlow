"use strict";

/**
 * Removes frontend/.angular so the Angular dev server / incremental builds do not reuse
 * stale template metadata (fixes "ghost" errors after fixing templates).
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const cacheDir = path.join(root, "frontend", ".angular");

try {
  fs.rmSync(cacheDir, { recursive: true, force: true });
  console.log("[clean-angular-cache] removed:", cacheDir);
} catch (e) {
  if (e && e.code !== "ENOENT") {
    console.warn("[clean-angular-cache]", e.message || e);
    process.exitCode = 0;
  }
}
