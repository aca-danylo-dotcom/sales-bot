import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

/* Бэкенд — тот же процесс, что и бот (bot.py), порт 8080 по умолчанию
   (config.WEB_PORT). В разработке фронт живёт на 5173 и ходит к нему через прокси. */
const BACKEND = process.env.CRM_BACKEND ?? "http://localhost:8080";

/* Прокси обязан переписать Origin. Панель защищена от чужих форм проверкой
   заголовка Origin (web/app.py, same_origin_only): сессий и токенов здесь нет,
   Origin — единственное, что браузер проставляет сам и что нельзя подделать со
   страницы. Без подмены сервер увидел бы Origin localhost:5173 при Host
   localhost:8080 и отдавал бы 403 на каждое сохранение — но только в dev. */
const proxy = {
  target: BACKEND,
  changeOrigin: true,
  headers: { Origin: BACKEND },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    /* Собранная панель ложится в web/dist — оттуда её отдаёт aiohttp.
       Имена файлов Vite хеширует сам, поэтому ручная метка ?v= больше не нужна. */
    outDir: path.resolve(__dirname, "../dist"),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": proxy,
      "/media": proxy,
      "/health": proxy,
    },
  },
});
