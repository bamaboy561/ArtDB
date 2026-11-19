import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built site lives in app/web_api/dist, where the server serves it (at /new on the real site),
// so every address in it is relative. In development the API runs on its own port and /api is forwarded.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../../app/web_api/dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
