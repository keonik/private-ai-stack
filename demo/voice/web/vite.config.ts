import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The FastAPI app serves the build from static/, and proxies the API in dev so the page can talk to a
// locally running backend without CORS.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  build: { outDir: "../static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8085" } },
});
