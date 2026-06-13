import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base "/" (default): the Python server serves dist/ at the site root,
// so built assets are referenced with absolute /assets/... paths.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Dev-only convenience: proxy API calls to the Python backend.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
