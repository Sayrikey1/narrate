import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the UI and proxies the API to a running `narrate serve`,
// so the dev server never needs its own copy of the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bound to IPv4 explicitly. Vite otherwise listens on [::1] only, so the
    // 127.0.0.1 URL that `just up` prints would not answer.
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8420", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
