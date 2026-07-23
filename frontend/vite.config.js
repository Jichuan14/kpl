import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { createReadStream, existsSync, statSync } from "node:fs";
import { resolve, sep } from "node:path";

function publishedAssets() {
  const publishedRoot = resolve(import.meta.dirname, "../analysis/published");

  return {
    name: "serve-published-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = decodeURIComponent((req.url || "").split("?", 1)[0]);
        const match = pathname.match(/^\/assets\/(data|heroes)\/(.+)$/);
        if (!match) return next();

        const filePath = resolve(publishedRoot, match[1], match[2]);
        if (!filePath.startsWith(`${publishedRoot}${sep}`) || !existsSync(filePath)) {
          res.statusCode = 404;
          return res.end("Published asset not found");
        }

        const stat = statSync(filePath);
        if (!stat.isFile()) {
          res.statusCode = 404;
          return res.end("Published asset not found");
        }

        res.setHeader(
          "Content-Type",
          filePath.endsWith(".json") ? "application/json; charset=utf-8" : "image/jpeg"
        );
        createReadStream(filePath).pipe(res);
      });
    },
  };
}

export default defineConfig({
  plugins: [vue(), publishedAssets()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        timeout: 900_000,
        proxyTimeout: 900_000,
      },
      "/health": "http://127.0.0.1:8000",
    },
  },
});
