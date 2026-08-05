import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      "server-only": new URL("./tests/support/server-only-stub.ts", import.meta.url).pathname,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/support/setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}", "tests/component/**/*.test.{ts,tsx}", "tests/integration/**/*.test.{ts,tsx}"],
    // Integration tests reach a real Postgres via WEBSITE_PG_* when the
    // environment provides one; they skip cleanly otherwise.
    pool: "forks",
  },
});
