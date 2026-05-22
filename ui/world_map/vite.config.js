import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    base: "/ui/",
    plugins: [react()],
    build: {
        outDir: "dist",
        emptyOutDir: true,
        sourcemap: false,
        rollupOptions: {
            output: {
                manualChunks: function (id) {
                    if (id.indexOf("maplibre-gl") >= 0 || id.indexOf("@maplibre") >= 0) {
                        return "satellite-map";
                    }
                },
            },
        },
    },
});
