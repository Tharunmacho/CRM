// vite.config.ts
import { defineConfig, loadEnv } from "file:///M:/crm/node_modules/vite/dist/node/index.js";
import react from "file:///M:/crm/node_modules/@vitejs/plugin-react/dist/index.js";
import path from "node:path";
var __vite_injected_original_dirname = "M:\\crm\\frontend";
var SCREENS = {
  "/": "src/screens/dashboard/dashboard.tsx",
  "/inbox": "src/screens/inbox/inbox.tsx",
  "/clients": "src/screens/clients/clients.tsx",
  "/jobs": "src/screens/jobs/jobs.tsx",
  "/add-candidate": "src/screens/candidates/add-candidate.tsx",
  "/data-bank": "src/screens/candidates/data-bank.tsx",
  "/leads": "src/screens/leads/leads.tsx",
  "/leads/import": "src/screens/leads/leads.tsx",
  "/tasks": "src/screens/tasks/tasks.tsx"
};
var SHELL = "src/app/layout/app-shell.tsx";
function earlyChunks(apiBase = "") {
  let base = "/";
  return {
    name: "crm-early-chunks",
    configResolved(config) {
      base = config.base.endsWith("/") ? config.base : `${config.base}/`;
    },
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        const bundle = ctx.bundle;
        const withBase = (text) => text.replace("__CRM_API_BASE__", JSON.stringify(apiBase).slice(1, -1));
        if (!bundle) return withBase(html.replace("__CRM_CHUNK_TABLE__", "{}"));
        const chunks = Object.values(bundle).filter((item) => item.type === "chunk");
        const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
        const closure = (start, seen = /* @__PURE__ */ new Set()) => {
          const pending = [start];
          while (pending.length) {
            const name = pending.pop();
            if (seen.has(name)) continue;
            seen.add(name);
            for (const next of byName.get(name)?.imports ?? []) pending.push(next);
          }
          return seen;
        };
        const entry = chunks.find((chunk) => chunk.isEntry);
        const inEntry = entry ? closure(entry.fileName) : /* @__PURE__ */ new Set();
        const find = (source) => chunks.find((chunk) => chunk.facadeModuleId?.replace(/\\/g, "/").endsWith(source));
        const files = (...sources) => {
          const names = /* @__PURE__ */ new Set();
          for (const source of sources) {
            const chunk = find(source);
            if (chunk) closure(chunk.fileName, names);
          }
          const urls = [];
          for (const name of names) {
            if (inEntry.has(name)) continue;
            urls.push(`${base}${name}`);
            for (const css of byName.get(name)?.viteMetadata?.importedCss ?? []) urls.push(`${base}${css}`);
          }
          return urls;
        };
        const table = {};
        for (const [route, source] of Object.entries(SCREENS)) table[route] = files(SHELL, source);
        return withBase(html.replace("__CRM_CHUNK_TABLE__", JSON.stringify(table)));
      }
    }
  };
}
var entryGraph = null;
function inEntryGraph(id, getModuleInfo) {
  if (!entryGraph) {
    const seen = /* @__PURE__ */ new Set();
    const pending = [path.resolve(__vite_injected_original_dirname, "src/main.tsx").split(path.sep).join("/")];
    while (pending.length) {
      const next = pending.pop();
      if (seen.has(next)) continue;
      seen.add(next);
      pending.push(...getModuleInfo(next)?.importedIds ?? []);
    }
    entryGraph = seen;
  }
  return entryGraph.has(id);
}
var vite_config_default = defineConfig(({ mode }) => {
  const env = loadEnv(mode, __vite_injected_original_dirname, "API_");
  const apiBase = loadEnv(mode, __vite_injected_original_dirname, "VITE_").VITE_API_URL ?? "";
  return {
    plugins: [react(), earlyChunks(apiBase)],
    resolve: { alias: { "@": path.resolve(__vite_injected_original_dirname, "./src") } },
    build: {
      rollupOptions: {
        output: {
          // Libraries change far less often than the app, so keeping them in their own files means a deploy
          // does not make every returning visitor download React again; charts stay out of every screen
          // except Reports.
          manualChunks(id, { getModuleInfo }) {
            if (!id.includes("node_modules")) return void 0;
            if (/node_modules\/(react|react-dom|scheduler|clsx|tailwind-merge)\//.test(id)) return "vendor-react";
            if (/node_modules\/(react-router|react-router-dom|@remix-run)\//.test(id)) return "vendor-router";
            if (/node_modules\/lucide-react\//.test(id)) return inEntryGraph(id, getModuleInfo) ? void 0 : "vendor-icons";
            if (/node_modules\/(recharts|d3-[^/]+|victory-vendor|recharts-scale|internmap|decimal\.js-light)\//.test(id)) return "vendor-charts";
            return void 0;
          }
        }
      }
    },
    server: {
      port: 5173,
      // Transform the shell and Candidate form before first navigation as well as
      // login. Only these entry graphs are warmed; optional dialogs remain lazy.
      warmup: {
        clientFiles: [
          "./index.html",
          "./src/main.tsx",
          "./src/screens/auth/login.tsx",
          "./src/app/layout/app-shell.tsx",
          "./src/screens/candidates/add-candidate.tsx"
        ]
      },
      proxy: {
        "/api": {
          target: env.API_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true
        }
      }
    }
  };
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJNOlxcXFxjcm1cXFxcZnJvbnRlbmRcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIk06XFxcXGNybVxcXFxmcm9udGVuZFxcXFx2aXRlLmNvbmZpZy50c1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vTTovY3JtL2Zyb250ZW5kL3ZpdGUuY29uZmlnLnRzXCI7aW1wb3J0IHsgZGVmaW5lQ29uZmlnLCBsb2FkRW52LCB0eXBlIFBsdWdpbiB9IGZyb20gJ3ZpdGUnXG5pbXBvcnQgcmVhY3QgZnJvbSAnQHZpdGVqcy9wbHVnaW4tcmVhY3QnXG5pbXBvcnQgcGF0aCBmcm9tICdub2RlOnBhdGgnXG5cbi8qKiBGaXJzdCBzY3JlZW4gb2YgZWFjaCB0b3AtbGV2ZWwgVVJMICh0aGUgZmlsZSBWaXRlIHR1cm5zIGludG8gdGhhdCByb3V0ZSdzIGNodW5rKS4gKi9cbmNvbnN0IFNDUkVFTlM6IFJlY29yZDxzdHJpbmcsIHN0cmluZz4gPSB7XG4gICcvJzogJ3NyYy9zY3JlZW5zL2Rhc2hib2FyZC9kYXNoYm9hcmQudHN4JyxcbiAgJy9pbmJveCc6ICdzcmMvc2NyZWVucy9pbmJveC9pbmJveC50c3gnLFxuICAnL2NsaWVudHMnOiAnc3JjL3NjcmVlbnMvY2xpZW50cy9jbGllbnRzLnRzeCcsXG4gICcvam9icyc6ICdzcmMvc2NyZWVucy9qb2JzL2pvYnMudHN4JyxcbiAgJy9hZGQtY2FuZGlkYXRlJzogJ3NyYy9zY3JlZW5zL2NhbmRpZGF0ZXMvYWRkLWNhbmRpZGF0ZS50c3gnLFxuICAnL2RhdGEtYmFuayc6ICdzcmMvc2NyZWVucy9jYW5kaWRhdGVzL2RhdGEtYmFuay50c3gnLFxuICAnL2xlYWRzJzogJ3NyYy9zY3JlZW5zL2xlYWRzL2xlYWRzLnRzeCcsXG4gICcvbGVhZHMvaW1wb3J0JzogJ3NyYy9zY3JlZW5zL2xlYWRzL2xlYWRzLnRzeCcsXG4gICcvdGFza3MnOiAnc3JjL3NjcmVlbnMvdGFza3MvdGFza3MudHN4Jyxcbn1cbmNvbnN0IFNIRUxMID0gJ3NyYy9hcHAvbGF5b3V0L2FwcC1zaGVsbC50c3gnXG5cbi8qKlxuICogTGV0cyBpbmRleC5odG1sIGZldGNoIHRoZSBhcHAgc2hlbGwgYW5kIHRoZSBmaXJzdCBzY3JlZW4ncyBjb2RlIGFsb25nc2lkZSB0aGUgZW50cnkgYnVuZGxlLlxuICpcbiAqIEJvdGggYXJlIGxhenkgY2h1bmtzLCBzbyB0aGUgYnJvd3NlciB3b3VsZCBvdGhlcndpc2UgZGlzY292ZXIgdGhlbSBvbmx5IGFmdGVyIHRoZSBlbnRyeSBidW5kbGUgcmFuLCB0aGVcbiAqIHNlc3Npb24gYW5zd2VyZWQgYW5kIHRoZSBzaGVsbCByZW5kZXJlZDogYSBjaGFpbiBvZiBkb3dubG9hZHMgYmVmb3JlIHRoZSBmaXJzdCBzY3JlZW4uIFRoZSBsaXN0IGJlbG93IGlzXG4gKiBidWlsdCBmcm9tIHRoZSByZWFsIGJ1bmRsZSAoY2h1bmsgbmFtZXMgYXJlIGhhc2hlZCksIHNvIGl0IGNhbiBuZXZlciBwb2ludCBhdCBhIHN0YWxlIGZpbGUuXG4gKi9cbmZ1bmN0aW9uIGVhcmx5Q2h1bmtzKGFwaUJhc2UgPSAnJyk6IFBsdWdpbiB7XG4gIGxldCBiYXNlID0gJy8nXG4gIHJldHVybiB7XG4gICAgbmFtZTogJ2NybS1lYXJseS1jaHVua3MnLFxuICAgIGNvbmZpZ1Jlc29sdmVkKGNvbmZpZykge1xuICAgICAgYmFzZSA9IGNvbmZpZy5iYXNlLmVuZHNXaXRoKCcvJykgPyBjb25maWcuYmFzZSA6IGAke2NvbmZpZy5iYXNlfS9gXG4gICAgfSxcbiAgICB0cmFuc2Zvcm1JbmRleEh0bWw6IHtcbiAgICAgIG9yZGVyOiAncG9zdCcsXG4gICAgICBoYW5kbGVyKGh0bWwsIGN0eCkge1xuICAgICAgICBjb25zdCBidW5kbGUgPSBjdHguYnVuZGxlXG4gICAgICAgIGNvbnN0IHdpdGhCYXNlID0gKHRleHQ6IHN0cmluZykgPT4gdGV4dC5yZXBsYWNlKCdfX0NSTV9BUElfQkFTRV9fJywgSlNPTi5zdHJpbmdpZnkoYXBpQmFzZSkuc2xpY2UoMSwgLTEpKVxuICAgICAgICBpZiAoIWJ1bmRsZSkgcmV0dXJuIHdpdGhCYXNlKGh0bWwucmVwbGFjZSgnX19DUk1fQ0hVTktfVEFCTEVfXycsICd7fScpKVxuICAgICAgICBjb25zdCBjaHVua3MgPSBPYmplY3QudmFsdWVzKGJ1bmRsZSkuZmlsdGVyKChpdGVtKSA9PiBpdGVtLnR5cGUgPT09ICdjaHVuaycpXG4gICAgICAgIGNvbnN0IGJ5TmFtZSA9IG5ldyBNYXAoY2h1bmtzLm1hcCgoY2h1bmspID0+IFtjaHVuay5maWxlTmFtZSwgY2h1bmtdKSlcbiAgICAgICAgY29uc3QgY2xvc3VyZSA9IChzdGFydDogc3RyaW5nLCBzZWVuID0gbmV3IFNldDxzdHJpbmc+KCkpID0+IHtcbiAgICAgICAgICBjb25zdCBwZW5kaW5nID0gW3N0YXJ0XVxuICAgICAgICAgIHdoaWxlIChwZW5kaW5nLmxlbmd0aCkge1xuICAgICAgICAgICAgY29uc3QgbmFtZSA9IHBlbmRpbmcucG9wKCkgYXMgc3RyaW5nXG4gICAgICAgICAgICBpZiAoc2Vlbi5oYXMobmFtZSkpIGNvbnRpbnVlXG4gICAgICAgICAgICBzZWVuLmFkZChuYW1lKVxuICAgICAgICAgICAgZm9yIChjb25zdCBuZXh0IG9mIGJ5TmFtZS5nZXQobmFtZSk/LmltcG9ydHMgPz8gW10pIHBlbmRpbmcucHVzaChuZXh0KVxuICAgICAgICAgIH1cbiAgICAgICAgICByZXR1cm4gc2VlblxuICAgICAgICB9XG4gICAgICAgIGNvbnN0IGVudHJ5ID0gY2h1bmtzLmZpbmQoKGNodW5rKSA9PiBjaHVuay5pc0VudHJ5KVxuICAgICAgICBjb25zdCBpbkVudHJ5ID0gZW50cnkgPyBjbG9zdXJlKGVudHJ5LmZpbGVOYW1lKSA6IG5ldyBTZXQ8c3RyaW5nPigpXG4gICAgICAgIGNvbnN0IGZpbmQgPSAoc291cmNlOiBzdHJpbmcpID0+IGNodW5rcy5maW5kKChjaHVuaykgPT4gY2h1bmsuZmFjYWRlTW9kdWxlSWQ/LnJlcGxhY2UoL1xcXFwvZywgJy8nKS5lbmRzV2l0aChzb3VyY2UpKVxuICAgICAgICBjb25zdCBmaWxlcyA9ICguLi5zb3VyY2VzOiBzdHJpbmdbXSkgPT4ge1xuICAgICAgICAgIGNvbnN0IG5hbWVzID0gbmV3IFNldDxzdHJpbmc+KClcbiAgICAgICAgICBmb3IgKGNvbnN0IHNvdXJjZSBvZiBzb3VyY2VzKSB7XG4gICAgICAgICAgICBjb25zdCBjaHVuayA9IGZpbmQoc291cmNlKVxuICAgICAgICAgICAgaWYgKGNodW5rKSBjbG9zdXJlKGNodW5rLmZpbGVOYW1lLCBuYW1lcylcbiAgICAgICAgICB9XG4gICAgICAgICAgY29uc3QgdXJsczogc3RyaW5nW10gPSBbXVxuICAgICAgICAgIGZvciAoY29uc3QgbmFtZSBvZiBuYW1lcykge1xuICAgICAgICAgICAgaWYgKGluRW50cnkuaGFzKG5hbWUpKSBjb250aW51ZVxuICAgICAgICAgICAgdXJscy5wdXNoKGAke2Jhc2V9JHtuYW1lfWApXG4gICAgICAgICAgICBmb3IgKGNvbnN0IGNzcyBvZiBieU5hbWUuZ2V0KG5hbWUpPy52aXRlTWV0YWRhdGE/LmltcG9ydGVkQ3NzID8/IFtdKSB1cmxzLnB1c2goYCR7YmFzZX0ke2Nzc31gKVxuICAgICAgICAgIH1cbiAgICAgICAgICByZXR1cm4gdXJsc1xuICAgICAgICB9XG4gICAgICAgIGNvbnN0IHRhYmxlOiBSZWNvcmQ8c3RyaW5nLCBzdHJpbmdbXT4gPSB7fVxuICAgICAgICBmb3IgKGNvbnN0IFtyb3V0ZSwgc291cmNlXSBvZiBPYmplY3QuZW50cmllcyhTQ1JFRU5TKSkgdGFibGVbcm91dGVdID0gZmlsZXMoU0hFTEwsIHNvdXJjZSlcbiAgICAgICAgcmV0dXJuIHdpdGhCYXNlKGh0bWwucmVwbGFjZSgnX19DUk1fQ0hVTktfVEFCTEVfXycsIEpTT04uc3RyaW5naWZ5KHRhYmxlKSkpXG4gICAgICB9LFxuICAgIH0sXG4gIH1cbn1cblxudHlwZSBNb2R1bGVMb29rdXAgPSAoaWQ6IHN0cmluZykgPT4geyBpbXBvcnRlZElkczogcmVhZG9ubHkgc3RyaW5nW10gfSB8IG51bGxcblxuLyoqIEV2ZXJ5IG1vZHVsZSB0aGUgZW50cnkgYnVuZGxlIGltcG9ydHMgc3RhdGljYWxseSAoZGlyZWN0bHkgb3IgdGhyb3VnaCBvdGhlcnMpLCBmb3VuZCBvbmNlIHBlciBidWlsZCBmcm9tIGBzcmMvbWFpbi50c3hgLiAqL1xubGV0IGVudHJ5R3JhcGg6IFNldDxzdHJpbmc+IHwgbnVsbCA9IG51bGxcbmZ1bmN0aW9uIGluRW50cnlHcmFwaChpZDogc3RyaW5nLCBnZXRNb2R1bGVJbmZvOiBNb2R1bGVMb29rdXApOiBib29sZWFuIHtcbiAgaWYgKCFlbnRyeUdyYXBoKSB7XG4gICAgY29uc3Qgc2VlbiA9IG5ldyBTZXQ8c3RyaW5nPigpXG4gICAgY29uc3QgcGVuZGluZyA9IFtwYXRoLnJlc29sdmUoX19kaXJuYW1lLCAnc3JjL21haW4udHN4Jykuc3BsaXQocGF0aC5zZXApLmpvaW4oJy8nKV1cbiAgICB3aGlsZSAocGVuZGluZy5sZW5ndGgpIHtcbiAgICAgIGNvbnN0IG5leHQgPSBwZW5kaW5nLnBvcCgpIGFzIHN0cmluZ1xuICAgICAgaWYgKHNlZW4uaGFzKG5leHQpKSBjb250aW51ZVxuICAgICAgc2Vlbi5hZGQobmV4dClcbiAgICAgIHBlbmRpbmcucHVzaCguLi4oZ2V0TW9kdWxlSW5mbyhuZXh0KT8uaW1wb3J0ZWRJZHMgPz8gW10pKVxuICAgIH1cbiAgICBlbnRyeUdyYXBoID0gc2VlblxuICB9XG4gIHJldHVybiBlbnRyeUdyYXBoLmhhcyhpZClcbn1cblxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKCh7IG1vZGUgfSkgPT4ge1xuICBjb25zdCBlbnYgPSBsb2FkRW52KG1vZGUsIF9fZGlybmFtZSwgJ0FQSV8nKVxuICBjb25zdCBhcGlCYXNlID0gbG9hZEVudihtb2RlLCBfX2Rpcm5hbWUsICdWSVRFXycpLlZJVEVfQVBJX1VSTCA/PyAnJ1xuXG4gIHJldHVybiB7XG4gICAgcGx1Z2luczogW3JlYWN0KCksIGVhcmx5Q2h1bmtzKGFwaUJhc2UpXSxcbiAgICByZXNvbHZlOiB7IGFsaWFzOiB7ICdAJzogcGF0aC5yZXNvbHZlKF9fZGlybmFtZSwgJy4vc3JjJykgfSB9LFxuICAgIGJ1aWxkOiB7XG4gICAgICByb2xsdXBPcHRpb25zOiB7XG4gICAgICAgIG91dHB1dDoge1xuICAgICAgICAgIC8vIExpYnJhcmllcyBjaGFuZ2UgZmFyIGxlc3Mgb2Z0ZW4gdGhhbiB0aGUgYXBwLCBzbyBrZWVwaW5nIHRoZW0gaW4gdGhlaXIgb3duIGZpbGVzIG1lYW5zIGEgZGVwbG95XG4gICAgICAgICAgLy8gZG9lcyBub3QgbWFrZSBldmVyeSByZXR1cm5pbmcgdmlzaXRvciBkb3dubG9hZCBSZWFjdCBhZ2FpbjsgY2hhcnRzIHN0YXkgb3V0IG9mIGV2ZXJ5IHNjcmVlblxuICAgICAgICAgIC8vIGV4Y2VwdCBSZXBvcnRzLlxuICAgICAgICAgIG1hbnVhbENodW5rcyhpZCwgeyBnZXRNb2R1bGVJbmZvIH0pIHtcbiAgICAgICAgICAgIGlmICghaWQuaW5jbHVkZXMoJ25vZGVfbW9kdWxlcycpKSByZXR1cm4gdW5kZWZpbmVkXG4gICAgICAgICAgICAvLyBjbHN4IGFuZCB0YWlsd2luZC1tZXJnZSBhcmUgdXNlZCBieSBldmVyeSBzY3JlZW4uIExlZnQgdW5hc3NpZ25lZCwgY2xzeCB3YXMgcHVsbGVkIGludG8gdGhlIGNoYXJ0cyBjaHVua1xuICAgICAgICAgICAgLy8gKGl0cyBvdGhlciBpbXBvcnRlciksIHNvIHRoZSBlbnRyeSBidW5kbGUgc3RhdGljYWxseSBpbXBvcnRlZCAzNzUgS0Igb2YgcmVjaGFydHMgb24gZXZlcnkgcGFnZSBsb2FkLlxuICAgICAgICAgICAgaWYgKC9ub2RlX21vZHVsZXNcXC8ocmVhY3R8cmVhY3QtZG9tfHNjaGVkdWxlcnxjbHN4fHRhaWx3aW5kLW1lcmdlKVxcLy8udGVzdChpZCkpIHJldHVybiAndmVuZG9yLXJlYWN0J1xuICAgICAgICAgICAgaWYgKC9ub2RlX21vZHVsZXNcXC8ocmVhY3Qtcm91dGVyfHJlYWN0LXJvdXRlci1kb218QHJlbWl4LXJ1bilcXC8vLnRlc3QoaWQpKSByZXR1cm4gJ3ZlbmRvci1yb3V0ZXInXG4gICAgICAgICAgICAvLyBsdWNpZGUtcmVhY3QgaXMgb25lIG1vZHVsZSBwZXIgaWNvbjsgbGVmdCBhbG9uZSBSb2xsdXAgbWFkZSBhIHRpbnkgY2h1bmsgcGVyIGljb24gKGFib3V0IDYwKSwgYW5kIGV2ZXJ5IHNjcmVlblxuICAgICAgICAgICAgLy8gbW9kdWxlcHJlbG9hZGVkIHRoZSBvbmVzIGl0IHVzZXMuIFRoZSBpY29ucyB0aGUgc2NyZWVucyB1c2UgZ28gaW50byBvbmUgc2hhcmVkIGNodW5rIChvbmUgcmVxdWVzdCwgY2FjaGVkIGFjcm9zc1xuICAgICAgICAgICAgLy8gc2NyZWVucykuIEFueXRoaW5nIHRoZSBlbnRyeSBidW5kbGUgYWxyZWFkeSBpbXBvcnRzIHN0YXlzIHdpdGggdGhlIGVudHJ5LCBzbyBmaXJzdCBsb2FkIGRvZXMgbm90IGRvd25sb2FkIGljb25zXG4gICAgICAgICAgICAvLyB0aGF0IG9ubHkgbGF6eSBzY3JlZW5zIHVzZS5cbiAgICAgICAgICAgIGlmICgvbm9kZV9tb2R1bGVzXFwvbHVjaWRlLXJlYWN0XFwvLy50ZXN0KGlkKSkgcmV0dXJuIGluRW50cnlHcmFwaChpZCwgZ2V0TW9kdWxlSW5mbykgPyB1bmRlZmluZWQgOiAndmVuZG9yLWljb25zJ1xuICAgICAgICAgICAgaWYgKC9ub2RlX21vZHVsZXNcXC8ocmVjaGFydHN8ZDMtW14vXSt8dmljdG9yeS12ZW5kb3J8cmVjaGFydHMtc2NhbGV8aW50ZXJubWFwfGRlY2ltYWxcXC5qcy1saWdodClcXC8vLnRlc3QoaWQpKSByZXR1cm4gJ3ZlbmRvci1jaGFydHMnXG4gICAgICAgICAgICByZXR1cm4gdW5kZWZpbmVkXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSxcbiAgICBzZXJ2ZXI6IHtcbiAgICAgIHBvcnQ6IDUxNzMsXG4gICAgICAvLyBUcmFuc2Zvcm0gdGhlIHNoZWxsIGFuZCBDYW5kaWRhdGUgZm9ybSBiZWZvcmUgZmlyc3QgbmF2aWdhdGlvbiBhcyB3ZWxsIGFzXG4gICAgICAvLyBsb2dpbi4gT25seSB0aGVzZSBlbnRyeSBncmFwaHMgYXJlIHdhcm1lZDsgb3B0aW9uYWwgZGlhbG9ncyByZW1haW4gbGF6eS5cbiAgICAgIHdhcm11cDoge1xuICAgICAgICBjbGllbnRGaWxlczogWycuL2luZGV4Lmh0bWwnLCAnLi9zcmMvbWFpbi50c3gnLCAnLi9zcmMvc2NyZWVucy9hdXRoL2xvZ2luLnRzeCcsXG4gICAgICAgICAgJy4vc3JjL2FwcC9sYXlvdXQvYXBwLXNoZWxsLnRzeCcsICcuL3NyYy9zY3JlZW5zL2NhbmRpZGF0ZXMvYWRkLWNhbmRpZGF0ZS50c3gnXSxcbiAgICAgIH0sXG4gICAgICBwcm94eToge1xuICAgICAgICAnL2FwaSc6IHtcbiAgICAgICAgICB0YXJnZXQ6IGVudi5BUElfUFJPWFlfVEFSR0VUIHx8ICdodHRwOi8vMTI3LjAuMC4xOjgwMDAnLFxuICAgICAgICAgIGNoYW5nZU9yaWdpbjogdHJ1ZSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSxcbiAgfVxufSlcbiJdLAogICJtYXBwaW5ncyI6ICI7QUFBcU8sU0FBUyxjQUFjLGVBQTRCO0FBQ3hSLE9BQU8sV0FBVztBQUNsQixPQUFPLFVBQVU7QUFGakIsSUFBTSxtQ0FBbUM7QUFLekMsSUFBTSxVQUFrQztBQUFBLEVBQ3RDLEtBQUs7QUFBQSxFQUNMLFVBQVU7QUFBQSxFQUNWLFlBQVk7QUFBQSxFQUNaLFNBQVM7QUFBQSxFQUNULGtCQUFrQjtBQUFBLEVBQ2xCLGNBQWM7QUFBQSxFQUNkLFVBQVU7QUFBQSxFQUNWLGlCQUFpQjtBQUFB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QVE7QUFDckIsWUFBTSxPQUFPLFFBQVEsSUFBSTtBQUN6QixVQUFJLEtBQUssSUFBSSxJQUFJLEVBQUc7QUFDcEIsV0FBSyxJQUFJLElBQUk7QUFDYixjQUFRLEtBQUssR0FBSSxjQUFjLElBQUksR0FBRyxlQUFlLENBQUMsQ0FBRTtBQUFBLElBQzFEO0FBQ0EsaUJBQWE7QUFBQSxFQUNmO0FBQ0EsU0FBTyxXQUFXLElBQUksRUFBRTtBQUMxQjtBQUVBLElBQU8sc0JBQVEsYUFBYSxDQUFDLEVBQUUsS0FBSyxNQUFNO0FBQ3hDLFFBQU0sTUFBTSxRQUFRLE1BQU0sa0NBQVcsTUFBTTtBQUMzQyxRQUFNLFVBQVUsUUFBUSxNQUFNLGtDQUFXLE9BQU8sRUFBRSxnQkFBZ0I7QUFFbEUsU0FBTztBQUFBLElBQ0wsU0FBUyxDQUFDLE1BQU0sR0FBRyxZQUFZLE9BQU8sQ0FBQztBQUFBLElBQ3ZDLFNBQVMsRUFBRSxPQUFPLEVBQUUsS0FBSyxLQUFLLFFBQVEsa0NBQVcsT0FBTyxFQUFFLEVBQUU7QUFBQSxJQUM1RCxPQUFPO0FBQUEsTUFDTCxlQUFlO0FBQUEsUUFDYixRQUFRO0FBQUE7QUFBQTtBQUFBO0FBQUEsVUFJTixhQUFhLElBQUksRUFBRSxjQUFjLEdBQUc7QUFDbEMsZ0JBQUksQ0FBQyxHQUFHLFNBQVMsY0FBYyxFQUFHLFFBQU87QUFHekMsZ0JBQUksa0VBQWtFLEtBQUssRUFBRSxFQUFHLFFBQU87QUFDdkYsZ0JBQUksNkRBQTZELEtBQUssRUFBRSxFQUFHLFFBQU87QUFLbEYsZ0JBQUksK0JBQStCLEtBQUssRUFBRSxFQUFHLFFBQU8sYUFBYSxJQUFJLGFBQWEsSUFBSSxTQUFZO0FBQ2xHLGdCQUFJLGdHQUFnRyxLQUFLLEVBQUUsRUFBRyxRQUFPO0FBQ3JILG1CQUFPO0FBQUEsVUFDVDtBQUFBLFFBQ0Y7QUFBQSxNQUNGO0FBQUEsSUFDRjtBQUFBLElBQ0EsUUFBUTtBQUFBLE1BQ04sTUFBTTtBQUFBO0FBQUE7QUFBQSxNQUdOLFFBQVE7QUFBQSxRQUNOLGFBQWE7QUFBQSxVQUFDO0FBQUEsVUFBZ0I7QUFBQSxVQUFrQjtBQUFBLFVBQzlDO0FBQUEsVUFBa0M7QUFBQSxRQUE0QztBQUFBLE1BQ2xGO0FBQUEsTUFDQSxPQUFPO0FBQUEsUUFDTCxRQUFRO0FBQUEsVUFDTixRQUFRLElBQUksb0JBQW9CO0FBQUEsVUFDaEMsY0FBYztBQUFBLFFBQ2hCO0FBQUEsTUFDRjtBQUFBLElBQ0Y7QUFBQSxFQUNGO0FBQ0YsQ0FBQzsiLAogICJuYW1lcyI6IFtdCn0K
