import Editor from '@monaco-editor/react';

import { Spinner } from '../../components/ui';
import { useThemeStore } from '../../lib/stores/themeStore';
import { languageForPath } from './languages';
import { monacoThemeFor } from './monacoSetup';

interface CodeEditorProps {
  path: string;
  value: string;
  readOnly: boolean;
  onChange: (value: string) => void;
}

export function CodeEditor({ path, value, readOnly, onChange }: CodeEditorProps): JSX.Element {
  // Monaco renders to a canvas and cannot follow CSS variables, so the theme is pushed to it.
  // Subscribing to `resolved` (not `mode`) means a `system` user's editor flips with the OS too.
  const resolved = useThemeStore((s) => s.resolved);

  return (
    <Editor
      // Keying by path gives each file its own model, so undo history and view state don't leak
      // across tabs.
      key={path}
      path={path}
      language={languageForPath(path)}
      theme={monacoThemeFor(resolved)}
      value={value}
      onChange={(next) => onChange(next ?? '')}
      loading={<Spinner />}
      options={{
        readOnly,
        fontSize: 12.5,
        lineHeight: 20,
        fontFamily: 'var(--font-mono)',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderLineHighlight: 'line',
        smoothScrolling: true,
        padding: { top: 10, bottom: 10 },
        // The editor sits inside a panel that already has a border; its own overlay ruler and
        // shadow would double it up.
        overviewRulerBorder: false,
        scrollbar: { verticalSliderSize: 8, horizontalSliderSize: 8, useShadows: false },
        guides: { indentation: true },
      }}
    />
  );
}
