/// <reference types="vite/client" />

// Baked at build time via vite.config.ts `define`. Compared against
// /version.json on an interval so the SPA can auto-reload after a deploy.
declare const __BUILD_VERSION__: string;
