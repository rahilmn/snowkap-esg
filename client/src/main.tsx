import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./index.css";

// Force clear all caches on app boot (v2 cache bust)
const CACHE_VERSION = "v5";
if (localStorage.getItem("snowkap-cache-version") !== CACHE_VERSION) {
  // Clear React Query cache, Zustand saved store, and old data
  localStorage.removeItem("snowkap-saved");
  localStorage.setItem("snowkap-cache-version", CACHE_VERSION);
  console.log("[Snowkap] Cache cleared for version", CACHE_VERSION);
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
      gcTime: 0,  // Don't keep unused data in cache
      retry: 1,
      refetchOnMount: "always",
      refetchOnWindowFocus: true,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
