import { AnimatePresence, motion } from 'framer-motion';
import { Monitor, Moon, Sun } from 'lucide-react';

import { useThemeStore, type ThemeMode } from '../../lib/stores/themeStore';

const ORDER: ThemeMode[] = ['dark', 'light', 'system'];

const LABELS: Record<ThemeMode, string> = {
  dark: 'Dark theme',
  light: 'Light theme',
  system: 'Match the OS theme',
};

const ICONS: Record<ThemeMode, typeof Sun> = {
  dark: Moon,
  light: Sun,
  system: Monitor,
};

/**
 * Cycles dark → light → system. One button rather than a menu: theme is a set-and-forget
 * preference, and the third press landing on "system" keeps OS-followers reachable without
 * burying anyone in a dropdown.
 */
export function ThemeToggle(): JSX.Element {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);
  const Icon = ICONS[mode];

  return (
    <button
      type="button"
      data-testid="theme-toggle"
      aria-label={LABELS[mode]}
      title={LABELS[mode]}
      onClick={() => setMode(ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length])}
      className="relative flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg"
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={mode}
          initial={{ opacity: 0, rotate: -30, scale: 0.6 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 30, scale: 0.6 }}
          transition={{ duration: 0.15 }}
          className="flex"
        >
          <Icon aria-hidden className="h-4 w-4" strokeWidth={1.75} />
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
