import { defineConfig } from 'astro/config';
import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';

// Server output: marketing pages opt back into static with `export const prerender = true`.
// The booking API + admin need a server (SQLite on disk, signed-cookie auth).
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  vite: { plugins: [tailwindcss()] },
});
