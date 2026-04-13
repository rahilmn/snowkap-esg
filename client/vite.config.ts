import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Thin dev config — proxies /api to the FastAPI server (Phase 9) on port 8000.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: parseInt(process.env.PORT || "5173"),
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: true,
    port: parseInt(process.env.PORT || "4173"),
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
