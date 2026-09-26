// Construit la page de l'appli dans www/ (dossier servi par Capacitor).
import { build } from 'esbuild';
import { cpSync, mkdirSync, rmSync } from 'node:fs';

rmSync('www', { recursive: true, force: true });
mkdirSync('www/fonts', { recursive: true });
cpSync('src/index.html', 'www/index.html');
cpSync('node_modules/@fontsource-variable/archivo/files/archivo-latin-wdth-normal.woff2', 'www/fonts/archivo.woff2');

await build({
  entryPoints: ['src/main.js'],
  bundle: true,
  outdir: 'www',
  format: 'esm',
  target: ['chrome100'],
  minify: !process.env.DEV,
  sourcemap: process.env.DEV ? 'inline' : false,
  legalComments: 'none',
  external: ['fonts/*'],
  logLevel: 'info',
});
