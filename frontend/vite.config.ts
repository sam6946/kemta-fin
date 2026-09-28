/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Le navigateur ne parle qu'au serveur Vite : /api est proxyfié vers Django.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: false,
    // Autorise le domaine de prévisualisation (proxy Arena/e2b) sans liste figée.
    allowedHosts: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/media": { target: API_TARGET, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
