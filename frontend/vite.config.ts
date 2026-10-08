import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon-192.png", "icon-512.png"],
      manifest: {
        name: "Chat LLM Local",
        short_name: "Chat Local",
        description: "Assistente com LLM local, RAG e OCR",
        lang: "pt-BR",
        start_url: "/",
        display: "standalone",
        background_color: "#111418",
        theme_color: "#111418",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" }
        ]
      },
      workbox: {
        // nunca cachear a API (respostas do chat e dados por usuário)
        navigateFallbackDenylist: [/^\/api\//]
      }
    })
  ],
  server: {
    proxy: { "/api": "http://localhost:8000" }
  }
});
