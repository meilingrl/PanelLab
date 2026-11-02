import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
// Exercise offset-free backend timestamps in a non-UTC host zone in CI too.
process.env.TZ = "Asia/Singapore";

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.js"],
    clearMocks: true,
  },
});
