/**
 * Shared field chrome, kept out of `Input.tsx` so that module exports components only (Fast Refresh
 * degrades to a full reload otherwise).
 *
 * The focus treatment is a border colour change plus the global focus ring rather than a glow —
 * glow on every input is the loudest "generated UI" tell, and it also washes out on a white canvas.
 */
export const fieldClasses = [
  'w-full rounded-md border border-line bg-inset text-primary placeholder:text-faint',
  'transition-[border-color,background-color] duration-fast ease-out',
  'hover:border-emphasis focus:border-brand',
  'disabled:cursor-not-allowed disabled:opacity-50',
].join(' ');
