import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { writeFileSync } from "fs";
import { resolve } from "path";
import { execSync } from "child_process";

// Baked at build time. Used for self-reload: the SPA periodically polls
// /version.json; if the server's version differs from this constant, the
// SPA reloads itself so users on a stale build pick up the new one without
// a manual hard-refresh.
const BUILD_VERSION = String(Date.now());

// The git commit this build was made from. Written into /version.json so
// anyone can open https://<server>/version.json and see exactly which
// commit is live; deploy.ps1 refuses to finish unless it matches the
// commit it just pulled.
function gitCommit(): string {
  try {
    return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "unknown";
  }
}
const BUILD_COMMIT = gitCommit();

export default defineConfig({
  plugins: [
    react(),
    {
      name: "write-version-json",
      // Fires after Vite finishes writing the bundle to dist/.
      closeBundle() {
        writeFileSync(
          resolve("dist", "version.json"),
          JSON.stringify({
            version: BUILD_VERSION,
            commit: BUILD_COMMIT,
            builtAt: new Date().toISOString(),
          }),
        );
      },
    },
  ],
  define: {
    __BUILD_VERSION__: JSON.stringify(BUILD_VERSION),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
