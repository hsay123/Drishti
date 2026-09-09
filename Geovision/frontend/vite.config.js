import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // listen on both 127.0.0.1 and ::1 — avoids IPv4/IPv6 refusal flaps
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000", // IPv4 literal — never resolves to ::1
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
