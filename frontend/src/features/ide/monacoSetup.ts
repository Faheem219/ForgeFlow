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

/**
 * Editor themes.
 *
 * Monaco paints to its own canvas and cannot read CSS custom properties, so the token values are
 * mirrored here as literals. They must stay in step with `src/app/theme/tokens.css` — a mismatch
 * shows up immediately as an editor whose background differs from the panel holding it.
 */
export const FORGEFLOW_DARK = 'forgeflow-dark';
export const FORGEFLOW_LIGHT = 'forgeflow-light';

export function monacoThemeFor(resolved: 'light' | 'dark'): string {
  return resolved === 'dark' ? FORGEFLOW_DARK : FORGEFLOW_LIGHT;
}

void loader.init().then((instance) => {
  instance.editor.defineTheme(FORGEFLOW_DARK, {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#0b0f18', // --surface
      'editorGutter.background': '#0b0f18',
      'editorLineNumber.foreground': '#5f6779', // --text-faint
      'editorLineNumber.activeForeground': '#8b93a5', // --text-muted
      'editor.lineHighlightBackground': '#141a26', // --elevated
      'editorCursor.foreground': '#818cf8', // --brand-hover
      'editor.selectionBackground': '#262f40', // --border-default
      'editorWidget.background': '#161d2b', // --overlay
      'editorWidget.border': '#262f40',
      'editorIndentGuide.background1': '#1b2230', // --border-subtle
    },
  });

  instance.editor.defineTheme(FORGEFLOW_LIGHT, {
    base: 'vs',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#ffffff', // --surface
      'editorGutter.background': '#ffffff',
      'editorLineNumber.foreground': '#8a92a1', // --text-faint
      'editorLineNumber.activeForeground': '#626b7d', // --text-muted
      'editor.lineHighlightBackground': '#f4f5f8', // --elevated
      'editorCursor.foreground': '#4f46e5', // --brand
      'editor.selectionBackground': '#dbe0ff',
      'editorWidget.background': '#ffffff', // --overlay
      'editorWidget.border': '#dbdee5',
      'editorIndentGuide.background1': '#e9ebf0', // --border-subtle
    },
  });
});
