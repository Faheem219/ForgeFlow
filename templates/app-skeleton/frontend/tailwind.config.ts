import type { Config } from 'tailwindcss'

// Tailwind is pre-configured so the codegen agent NEVER sets up styling from scratch (token saver).
// Add design tokens (colors, fonts) here when a design artifact calls for them.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
} satisfies Config
