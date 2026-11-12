import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies API paths to FastAPI, so the browser only ever talks
// to one origin and no CORS setup is needed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/users": "http://localhost:8000",
      "/ask": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/integrations": "http://localhost:8000",
    },
  },
});
