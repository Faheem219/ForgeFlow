# `src/features/` — where feature code goes

Each user-facing feature is a self-contained folder. **The codegen agent writes here**; it never
touches the scaffold (Vite/Tailwind/router config, the API client, the layout).

```
src/features/<feature>/
  <Feature>Page.tsx     # the page component (registered in src/routes.tsx)
  components/            # feature-scoped components
  api.ts                # feature API calls — import { api } from '../../lib/api'
  types.ts              # feature types (mirror the backend Zod schemas)
```

Conventions:

- **Routing:** register the page in [`src/routes.tsx`](../routes.tsx) as a child of `Layout`
  (add it at the `FORGEFLOW:ROUTES` marker).
- **API:** never call `fetch` directly — use the typed client in [`src/lib/api.ts`](../lib/api.ts)
  so the backend base URL stays centralized.
- **Styling:** Tailwind utility classes only; no CSS files needed.
- **Tests:** colocate `*.test.tsx` (Vitest + Testing Library) next to the component.
