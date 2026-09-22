import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom", "remotion"] },
  server: { proxy: { "/api": "http://127.0.0.1:4831" }, fs: { allow: [".."] } },
});
