import Editor from '@monaco-editor/react';

import { Spinner } from '../../components/ui';
import { useThemeStore } from '../../lib/stores/themeStore';
import { languageForPath } from './languages';
import { MONACO_THEMES } from './monacoSetup';

interface CodeEditorProps {
  path: string;
  value: string;
  readOnly: boolean;
  onChange: (value: string) => void;
}

export function CodeEditor({ path, value, readOnly, onChange }: CodeEditorProps): JSX.Element {
  // Follow the app theme live — both monaco themes are registered up front (monacoSetup.ts).
  const resolved = useThemeStore((s) => s.resolved);

  return (
    <Editor
      // Keying by path gives each file its own model, so undo history and view state don't leak
      // across tabs.
      key={path}
      path={path}
      language={languageForPath(path)}
      theme={MONACO_THEMES[resolved]}
      value={value}
      onChange={(next) => onChange(next ?? '')}
      loading={
        <span className="text-fg-faint">
          <Spinner />
        </span>
      }
      // The webfont may land after Monaco first measures its glyphs; re-measuring once it has
      // loaded keeps the cursor and selection aligned with the characters actually drawn.
      onMount={(_editor, monaco) => {
        void document.fonts?.ready.then(() => monaco.editor.remeasureFonts());
      }}
      options={{
        readOnly,
        fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 13,
        lineHeight: 20,
        fontLigatures: false,
        padding: { top: 10, bottom: 10 },
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderLineHighlight: 'line',
        smoothScrolling: true,
        cursorBlinking: 'smooth',
        cursorSmoothCaretAnimation: 'on',
        overviewRulerBorder: false,
        hideCursorInOverviewRuler: true,
        guides: { indentation: true, bracketPairs: false },
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10, useShadows: false },
        stickyScroll: { enabled: false },
      }}
    />
  );
}
