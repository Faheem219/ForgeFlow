import type { ManifestOptions } from 'vite-plugin-pwa';

// PWA manifest (D14 — ForgeFlow itself is an installable PWA). Consumed by vite.config.ts
// and asserted by manifest.test.ts.
export const pwaManifest: Partial<ManifestOptions> = {
  // A stable `id` keeps the install identity fixed even if start_url ever changes (phase-49).
  id: '/',
  name: 'ForgeFlow',
  short_name: 'ForgeFlow',
  description: 'AI-native, human-in-the-loop app builder.',
  lang: 'en',
  dir: 'ltr',
  theme_color: '#312e81',
  background_color: '#020617',
  display: 'standalone',
  orientation: 'any',
  categories: ['developer', 'productivity'],
  start_url: '/',
  scope: '/',
  icons: [
    { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
    { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
    // A 512 maskable covers Android adaptive icons — the launcher downscales it as needed.
    { src: 'maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
  ],
};
