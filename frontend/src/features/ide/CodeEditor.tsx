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
      loading={<Spinner />}
      options={{
        readOnly,
        fontSize: 13,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderLineHighlight: 'line',
        smoothScrolling: true,
      }}
    />
  );
}
