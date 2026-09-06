import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    proxy: Object.fromEntries(["/auth", "/me", "/datasets", "/jobs", "/documents", "/settings", "/chat", "/conversations"].map((path) => [path, { target: "http://127.0.0.1:8080", changeOrigin: false }])),
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
  },
});
