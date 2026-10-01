/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// In development the console runs on :5173 and proxies API calls to the FastAPI server, so the browser
// sees one origin exactly as in production (where FastAPI serves the built files from frontend/dist).
const api = process.env.PAYGUARD_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { "/v1": api, "/healthz": api, "/readyz": api },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: { manualChunks: { react: ["react", "react-dom", "react-router-dom"], charts: ["recharts"] } },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
