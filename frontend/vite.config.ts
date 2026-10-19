import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { writeFileSync } from "fs";
import { resolve } from "path";

// Baked at build time. Used for self-reload: the SPA periodically polls
// /version.json; if the server's version differs from this constant, the
// SPA reloads itself so users on a stale build pick up the new one without
// a manual hard-refresh.
const BUILD_VERSION = String(Date.now());

export default defineConfig({
  plugins: [
    react(),
    {
      name: "write-version-json",
      // Fires after Vite finishes writing the bundle to dist/.
      closeBundle() {
        writeFileSync(
          resolve("dist", "version.json"),
          JSON.stringify({ version: BUILD_VERSION }),
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
