import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/predict": {
        target: "http://localhost:5001",
        changeOrigin: true,
      },
      "/ws": {
        target: "http://localhost:8001",
        ws: true,
      },
    },
  },
});
