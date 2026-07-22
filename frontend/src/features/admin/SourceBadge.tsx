import { Badge } from '../../components/ui';
import type { SettingSource } from './types';

// The source badge makes precedence *visible* (the user's `admin > env > default` requirement):
// an operator always knows whether a value comes from the admin panel (db), env, or the code
// default. `db` is highlighted because it means "an admin has overridden this here".
const TONES = {
  db: 'brand',
  env: 'neutral',
  default: 'neutral',
} as const;

const LABELS: Record<SettingSource, string> = {
  db: 'admin',
  env: 'env',
  default: 'default',
};

export function SourceBadge({ source }: { source: SettingSource }): JSX.Element {
  return (
    <span data-testid={`source-${source}`} title={`Value resolved from: ${source}`}>
      <Badge tone={TONES[source]}>{LABELS[source]}</Badge>
    </span>
  );
}
