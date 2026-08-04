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

/** Dark theme matching the workspace palette (slate/indigo). */
export const FORGEFLOW_DARK = 'forgeflow-dark';

loader.init().then((instance) => {
  instance.editor.defineTheme(FORGEFLOW_DARK, {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#0f172a', // slate-900
      'editorGutter.background': '#0f172a',
      'editorLineNumber.foreground': '#475569',
      'editor.lineHighlightBackground': '#1e293b',
      'editorCursor.foreground': '#a5b4fc', // indigo-300
      'editor.selectionBackground': '#334155',
    },
  });
});
