// Side-effect module: bind @monaco-editor/react to a LOCALLY BUNDLED monaco.
//
// By default the loader pulls monaco from a CDN at runtime, which would break the PWA offline
// (and add a third-party origin). Pointing the loader at the bundled copy and wiring the language
// workers ourselves keeps everything self-hosted. Imported only from the lazy IDE chunk.

import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

import { dark, light, type ResolvedTheme } from '../../app/theme/palette';

const environment: monaco.Environment = {
  getWorker(_moduleId: string, label: string): Worker {
    switch (label) {
      case 'json':
        return new jsonWorker();
      case 'css':
      case 'scss':
      case 'less':
        return new cssWorker();
      case 'html':
      case 'handlebars':
      case 'razor':
        return new htmlWorker();
      case 'typescript':
      case 'javascript':
        return new tsWorker();
      default:
        return new editorWorker();
    }
  },
};

(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = environment;

loader.config({ monaco });

// Monaco renders to canvas and cannot read CSS custom properties, so both themes are defined up
// front from palette.ts (the same source the CSS variables come from) and switching is just
// `setTheme` — no re-registration, nothing to drift.
export const FORGEFLOW_DARK = 'forgeflow-dark';
export const FORGEFLOW_LIGHT = 'forgeflow-light';

export const MONACO_THEMES: Record<ResolvedTheme, string> = {
  dark: FORGEFLOW_DARK,
  light: FORGEFLOW_LIGHT,
};

loader.init().then((instance) => {
  instance.editor.defineTheme(FORGEFLOW_DARK, {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': dark.sunken,
      'editorGutter.background': dark.sunken,
      'editorLineNumber.foreground': dark.fgFaint,
      'editor.lineHighlightBackground': dark.raised,
      'editorCursor.foreground': dark.brandText,
      'editor.selectionBackground': dark.edgeStrong,
    },
  });
  instance.editor.defineTheme(FORGEFLOW_LIGHT, {
    base: 'vs',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': light.surface,
      'editorGutter.background': light.surface,
      'editorLineNumber.foreground': light.fgFaint,
      'editor.lineHighlightBackground': light.raised,
      'editorCursor.foreground': light.brand,
      'editor.selectionBackground': light.edge,
    },
  });
});
