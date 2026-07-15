// Extension → Monaco language id. The generated-app stack is fixed (React+Vite+TS / Express+TS /
// Mongoose), so the list is deliberately small — it covers what ForgeFlow actually emits.

const BY_EXTENSION: Record<string, string> = {
  ts: 'typescript',
  tsx: 'typescript',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  json: 'json',
  html: 'html',
  css: 'css',
  scss: 'scss',
  md: 'markdown',
  yml: 'yaml',
  yaml: 'yaml',
  sh: 'shell',
  sql: 'sql',
};

const BY_FILENAME: Record<string, string> = {
  dockerfile: 'dockerfile',
  '.env': 'shell',
  '.gitignore': 'plaintext',
};

export function languageForPath(path: string): string {
  const name = (path.split('/').pop() ?? '').toLowerCase();
  if (BY_FILENAME[name]) return BY_FILENAME[name];
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return 'plaintext'; // no extension, or a dotfile like `.babelrc`
  return BY_EXTENSION[name.slice(dot + 1)] ?? 'plaintext';
}
