/**
 * Fuzzy matching for the command palette.
 *
 * Subsequence, not substring: "dsh" should find "Dashboard", the way every editor's fuzzy-open
 * behaves. Anything stricter feels broken to the audience this tool is aimed at.
 *
 * Lives in its own module so `CommandPalette.tsx` exports components only (Fast Refresh), and so
 * the matching rule is testable without mounting the palette.
 */
export function subsequenceMatch(text: string, query: string): boolean {
  if (!query) return true;
  const haystack = text.toLowerCase();
  const needle = query.toLowerCase();
  let i = 0;
  for (const char of haystack) {
    if (char === needle[i]) i += 1;
    if (i === needle.length) return true;
  }
  return i === needle.length;
}
