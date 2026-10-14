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

/*
 * Syntax colours are tuned to the app palette rather than inherited from VS Code: the default
 * dark+ ramp (saturated blue keywords, orange strings) on a warm grey surface looked pasted in.
 * Hues stay distinct by role — keyword violet, string green, number amber, type teal — but at the
 * same calm chroma as the rest of the UI. Comments sit at the faint text tone, italic.
 */
const DARK_TOKENS = {
  keyword: 'b6a8f5',
  string: '9fd3a8',
  number: 'e6b673',
  type: '7cc4d4',
  func: 'e9d6a6',
  comment: '6e6b76',
  variable: 'ecebef',
  tag: 'f2a29b',
  attr: 'c9b8f0',
  delimiter: 'a29fab',
};
const LIGHT_TOKENS = {
  keyword: '5b47c4',
  string: '2f7a45',
  number: 'a35a00',
  type: '0f6f86',
  func: '7a5410',
  comment: '94919d',
  variable: '1f1d24',
  tag: 'b8322b',
  attr: '6a4fc0',
  delimiter: '6b6874',
};

function rules(t: typeof DARK_TOKENS) {
  return [
    { token: 'comment', foreground: t.comment, fontStyle: 'italic' },
    { token: 'keyword', foreground: t.keyword },
    { token: 'keyword.json', foreground: t.keyword },
    { token: 'string', foreground: t.string },
    { token: 'string.key.json', foreground: t.attr },
    { token: 'string.value.json', foreground: t.string },
    { token: 'number', foreground: t.number },
    { token: 'regexp', foreground: t.number },
    { token: 'type', foreground: t.type },
    { token: 'type.identifier', foreground: t.type },
    { token: 'identifier', foreground: t.variable },
    { token: 'delimiter', foreground: t.delimiter },
    { token: 'delimiter.bracket', foreground: t.delimiter },
    { token: 'tag', foreground: t.tag },
    { token: 'metatag', foreground: t.tag },
    { token: 'attribute.name', foreground: t.attr },
    { token: 'attribute.value', foreground: t.string },
  ];
}

loader.init().then((instance) => {
  instance.editor.defineTheme(FORGEFLOW_DARK, {
    base: 'vs-dark',
    inherit: true,
    rules: rules(DARK_TOKENS),
    colors: {
      // The editor shares its pane's surface, so the IDE reads as one object rather than a dark
      // hole cut into a lighter frame.
      'editor.background': dark.surface,
      'editor.foreground': dark.fg,
      'editorGutter.background': dark.surface,
      'editorLineNumber.foreground': dark.fgFaint,
      'editorLineNumber.activeForeground': dark.fgMuted,
      'editor.lineHighlightBackground': '#ffffff08',
      'editor.lineHighlightBorder': '#00000000',
      'editorCursor.foreground': dark.brandText,
      'editor.selectionBackground': '#5b5bd640',
      'editor.inactiveSelectionBackground': '#5b5bd626',
      'editorIndentGuide.background1': '#ffffff0d',
      'editorIndentGuide.activeBackground1': '#ffffff1f',
      'editorWhitespace.foreground': '#ffffff14',
      'editorBracketMatch.background': '#5b5bd626',
      'editorBracketMatch.border': '#5b5bd666',
      'scrollbarSlider.background': '#ffffff12',
      'scrollbarSlider.hoverBackground': '#ffffff22',
      'scrollbarSlider.activeBackground': '#ffffff30',
      'editorWidget.background': dark.overlay,
      'editorWidget.border': dark.edgeStrong,
      'editorSuggestWidget.background': dark.overlay,
      'editorSuggestWidget.border': dark.edgeStrong,
      'editorSuggestWidget.selectedBackground': dark.raised,
      'editorHoverWidget.background': dark.overlay,
      'editorHoverWidget.border': dark.edgeStrong,
      focusBorder: '#00000000',
    },
  });
  instance.editor.defineTheme(FORGEFLOW_LIGHT, {
    base: 'vs',
    inherit: true,
    rules: rules(LIGHT_TOKENS),
    colors: {
      'editor.background': light.surface,
      'editor.foreground': light.fg,
      'editorGutter.background': light.surface,
      'editorLineNumber.foreground': light.fgFaint,
      'editorLineNumber.activeForeground': light.fgMuted,
      'editor.lineHighlightBackground': '#1f1d2408',
      'editor.lineHighlightBorder': '#00000000',
      'editorCursor.foreground': light.brand,
      'editor.selectionBackground': '#5b5bd62e',
      'editor.inactiveSelectionBackground': '#5b5bd61a',
      'editorIndentGuide.background1': '#1f1d2412',
      'editorIndentGuide.activeBackground1': '#1f1d2426',
      'editorBracketMatch.background': '#5b5bd61a',
      'editorBracketMatch.border': '#5b5bd655',
      'scrollbarSlider.background': '#1f1d2414',
      'scrollbarSlider.hoverBackground': '#1f1d2424',
      'scrollbarSlider.activeBackground': '#1f1d2433',
      'editorWidget.background': light.overlay,
      'editorWidget.border': light.edgeStrong,
      'editorSuggestWidget.background': light.overlay,
      'editorSuggestWidget.border': light.edgeStrong,
      'editorSuggestWidget.selectedBackground': light.raised,
      focusBorder: '#00000000',
    },
  });
});
