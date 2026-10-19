import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
    root: "web/workbench",
    base: "/workbench/",
    plugins: [react()],
    build: { outDir: "dist", emptyOutDir: true },
    server: {
        proxy: Object.fromEntries(
            [
                "/api",
                "/replay",
                "/leaderboard",
                "/legacy",
                "/assets",
                "/app.js",
                "/styles.css",
            ].map((path) => [path, "http://127.0.0.1:8000"]),
        ),
    },
    test: {
        environment: "jsdom",
        setupFiles: ["./src/test-setup.ts"],
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
