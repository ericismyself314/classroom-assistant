// In-process alternative for environments that cannot spawn esbuild's worker.
// Same Vite/Rollup bundler and sources; TypeScript handles JSX transpilation.
import { build } from 'vite';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

await build({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  base: './',
  esbuild: false,
  resolve: { preserveSymlinks: true },
  plugins: [{ name: 'local-typescript', enforce: 'pre', transform(source, id) {
    if (!/\.[jt]sx?$/.test(id)) return;
    source = source.replace(/\bprocess\.env\.NODE_ENV\b/g, '"production"');
    if (id.includes('node_modules')) return { code: source, map: null };
    return { code: ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.ReactJSX, sourceMap: false,
    }, fileName: id }).outputText, map: null };
  }}],
  build: { outDir: 'dist', emptyOutDir: true, minify: false, cssMinify: false, chunkSizeWarningLimit: 1500 },
});
