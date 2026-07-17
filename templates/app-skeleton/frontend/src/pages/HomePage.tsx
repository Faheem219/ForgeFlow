// Example page. Replace/extend with real feature pages (see src/features/README.md). The Tailwind
// classes below prove styling works out of the box — no styling setup is ever generated.
export function HomePage() {
  return (
    <section className="space-y-4">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900">Your app starts here</h1>
      <p className="text-slate-600">
        This is the ForgeFlow generated-app skeleton: React + Vite + TypeScript + Tailwind on the
        frontend, Express + Mongoose on the backend. Feature code is added under{' '}
        <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-sm">src/features/</code>.
      </p>
      <a
        className="inline-flex items-center rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-500"
        href="https://vitejs.dev"
        target="_blank"
        rel="noreferrer"
      >
        Learn Vite
      </a>
    </section>
  )
}
