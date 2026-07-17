/// <reference types="vite/client" />

// Typed access to the app's env contract (see .env.example). Extend this when the agent adds
// new VITE_-prefixed variables so `import.meta.env.X` stays type-checked.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
