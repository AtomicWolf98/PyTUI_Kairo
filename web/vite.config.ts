import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const versionSource = readFileSync(resolve(__dirname, "../agent/_version.py"), "utf8");
const versionMatch = versionSource.match(/__version__\s*=\s*["']([^"']+)["']/);
if (!versionMatch) {
  throw new Error("Unable to read Kairo version from agent/_version.py");
}
const kairoVersion = versionMatch[1];

export default defineConfig({
  plugins: [
    react(),
    {
      name: "kairo-release-metadata",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "version.json",
          source: `${JSON.stringify({ version: kairoVersion }, null, 2)}\n`
        });
      }
    }
  ],
  build: {
    outDir: "../agent/web/static",
    emptyOutDir: true
  },
  test: {
    environment: "jsdom"
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765"
    }
  }
});
