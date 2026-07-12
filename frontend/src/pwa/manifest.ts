import type { ManifestOptions } from 'vite-plugin-pwa';

// PWA manifest (D14 — ForgeFlow itself is an installable PWA). Consumed by vite.config.ts
// and asserted by manifest.test.ts.
export const pwaManifest: Partial<ManifestOptions> = {
  name: 'ForgeFlow',
  short_name: 'ForgeFlow',
  description: 'AI-native, human-in-the-loop app builder.',
  theme_color: '#312e81',
  background_color: '#020617',
  display: 'standalone',
  start_url: '/',
  scope: '/',
  icons: [
    { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
    { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
    { src: 'maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
  ],
};
