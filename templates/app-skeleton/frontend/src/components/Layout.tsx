import { Link, Outlet } from 'react-router-dom'

// App shell. Feature pages render inside <Outlet />. Extend the nav as the agent adds routes.
export function Layout() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <nav className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-lg font-semibold text-indigo-600">
            ForgeFlow App
          </Link>
        </nav>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">
        <Outlet />
      </main>
    </div>
  )
}
