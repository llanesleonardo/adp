import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../app/static/exercise",
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: "src/exercise.jsx",
      output: {
        inlineDynamicImports: true,
        entryFileNames: "exercise.js",
        assetFileNames: "exercise.[ext]",
      },
    },
  },
});
