import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const backend = process.env.VITE_BACKEND_ORIGIN || "http://127.0.0.1:8080";

export default defineConfig({
  // 相对路径：dist/index.html 用 file:// 直接打开或部署在子目录时，
  // 资源仍能加载（绝对路径 /assets/... 会 404，导致页面完全没有样式）。
  base: "./",
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest) => proxyRequest.removeHeader("origin"));
        },
      },
      "/manual/api": {
        target: backend,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest) => proxyRequest.removeHeader("origin"));
        },
      },
    },
  },
});
