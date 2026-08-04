import Editor from '@monaco-editor/react';

import { Spinner } from '../../components/ui';
import { languageForPath } from './languages';
import { FORGEFLOW_DARK } from './monacoSetup';

interface CodeEditorProps {
  path: string;
  value: string;
  readOnly: boolean;
  onChange: (value: string) => void;
}

export function CodeEditor({ path, value, readOnly, onChange }: CodeEditorProps): JSX.Element {
  return (
    <Editor
      // Keying by path gives each file its own model, so undo history and view state don't leak
      // across tabs.
      key={path}
      path={path}
      language={languageForPath(path)}
      theme={FORGEFLOW_DARK}
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
