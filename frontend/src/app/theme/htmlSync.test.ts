import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { THEME_STORAGE_KEY } from '../../lib/stores/themeStore';
import { dark, light } from './palette';

/**
 * The pre-paint script in index.html runs before any module loads, so it necessarily duplicates
 * two palette hexes and the storage key as string literals. This test is the tripwire that keeps
 * those literals bound to their sources — change palette.ts or the storage key without updating
 * index.html and CI fails, instead of the browser chrome silently drifting from the canvas.
 */
describe('index.html pre-paint script', () => {
  const html = readFileSync(resolve(__dirname, '../../../index.html'), 'utf8');

  it('uses the same canvas hexes as palette.ts for the theme-color swap', () => {
    expect(html.toLowerCase()).toContain(`'${light.canvas.toLowerCase()}'`);
    expect(html.toLowerCase()).toContain(`'${dark.canvas.toLowerCase()}'`);
  });

  it('boots with the dark canvas as the static theme-color', () => {
    expect(html.toLowerCase()).toContain(
      `<meta name="theme-color" content="${dark.canvas.toLowerCase()}"`,
    );
  });

  it('reads the exact storage key themeStore writes', () => {
    expect(html).toContain(`localStorage.getItem('${THEME_STORAGE_KEY}')`);
  });
});
