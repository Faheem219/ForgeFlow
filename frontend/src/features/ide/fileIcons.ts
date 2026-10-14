import { Braces, FileCode2, FileJson2, FileText, FileType2, Image, Palette } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/**
 * A file's glyph and tint, from its extension. The tint is the cheapest navigation aid an explorer
 * has — TypeScript, JSON and styles separate at a glance down a long tree — and it is drawn from the
 * semantic palette, so it themes with everything else rather than importing VS Code's hexes.
 */
export function fileIcon(path: string): { icon: LucideIcon; tone: string } {
  const name = path.split('/').pop() ?? path;
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
  switch (ext) {
    case 'ts':
    case 'tsx':
      return { icon: FileCode2, tone: 'text-info' };
    case 'js':
    case 'jsx':
    case 'mjs':
    case 'cjs':
      return { icon: FileCode2, tone: 'text-warning' };
    case 'json':
      return { icon: FileJson2, tone: 'text-warning' };
    case 'css':
    case 'scss':
      return { icon: Palette, tone: 'text-accent' };
    case 'html':
      return { icon: Braces, tone: 'text-danger' };
    case 'md':
    case 'txt':
      return { icon: FileText, tone: 'text-fg-subtle' };
    case 'svg':
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
      return { icon: Image, tone: 'text-success' };
    case 'yml':
    case 'yaml':
    case 'env':
      return { icon: FileType2, tone: 'text-fg-subtle' };
    default:
      return { icon: FileText, tone: 'text-fg-subtle' };
  }
}
