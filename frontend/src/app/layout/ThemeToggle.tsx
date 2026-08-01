import { Segmented, Tooltip } from '../../components/ui';
import { IconMonitor, IconMoon, IconSun } from '../../components/ui/Icons';
import { useThemeStore, type ThemeMode } from '../../lib/stores/themeStore';

/**
 * Theme control. Three states, not two: `system` is a real choice — it means "keep following my
 * OS", which a binary toggle silently discards the first time the OS flips.
 */
export function ThemeToggle(): JSX.Element {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);

  return (
    <Segmented<ThemeMode>
      ariaLabel="Colour theme"
      size="sm"
      value={mode}
      onChange={setMode}
      options={[
        { value: 'light', label: <IconSun size={13} />, title: 'Light' },
        { value: 'dark', label: <IconMoon size={13} />, title: 'Dark' },
        { value: 'system', label: <IconMonitor size={13} />, title: 'Match system' },
      ]}
    />
  );
}

/** Single-button variant for tight chrome (auth screens). Flips relative to what is shown. */
export function ThemeToggleButton(): JSX.Element {
  const resolved = useThemeStore((s) => s.resolved);
  const toggle = useThemeStore((s) => s.toggle);
  const next = resolved === 'dark' ? 'light' : 'dark';

  return (
    <Tooltip label={`Switch to ${next} theme`}>
      <button
        type="button"
        onClick={toggle}
        aria-label={`Switch to ${next} theme`}
        data-testid="theme-toggle"
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors duration-fast ease-out hover:bg-elevated hover:text-primary"
      >
        {resolved === 'dark' ? <IconMoon size={14} /> : <IconSun size={14} />}
      </button>
    </Tooltip>
  );
}
