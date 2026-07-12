import { describe, expect, it } from 'vitest';

import { pwaManifest } from './manifest';

describe('pwaManifest', () => {
  it('declares the fields required for installability', () => {
    expect(pwaManifest.name).toBe('ForgeFlow');
    expect(pwaManifest.theme_color).toBeTruthy();
    expect(pwaManifest.background_color).toBeTruthy();
    expect(pwaManifest.display).toBe('standalone');

    const icons = pwaManifest.icons ?? [];
    const sizes = icons.map((i) => i.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
    expect(icons.some((i) => i.purpose === 'maskable')).toBe(true);
  });
});
