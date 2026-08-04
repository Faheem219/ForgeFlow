import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Compose class names with conflict resolution: later Tailwind utilities win over earlier ones
 * (`cn('p-2', condition && 'p-4')` → `p-4`), so variant + override props merge predictably.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
