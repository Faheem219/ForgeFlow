/* eslint-disable react-refresh/only-export-components */
import { Suspense, lazy } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router-dom';

import type { ReactNode } from 'react';

import Login from '../features/auth/Login';
import Register from '../features/auth/Register';
import { RequireAdmin, RequireAuth } from '../features/auth/guards';
import Dashboard from '../features/dashboard/Dashboard';
import NotFound from './NotFound';
import { AppShell } from './layout/AppShell';

// The landing page carries GSAP + its scroll plugins — signed-in users going straight to the
// app never pay for it.
const Landing = lazy(() => import('../features/landing/Landing'));

// Every other screen is its own chunk too: the first paint after sign-in is the dashboard, and it
// should not wait on the workspace, the evaluation charts, the admin catalogue and two dev pages.
// (Login, Register, the dashboard and the 404 stay eager — they are the common first screens.)
const Workspace = lazy(() => import('../features/workspace/Workspace'));
const EvalDashboard = lazy(() => import('../features/eval/EvalDashboard'));
const Settings = lazy(() => import('../features/settings/Settings'));
const AdminDashboard = lazy(() => import('../features/admin/AdminDashboard'));
const UiKit = lazy(() => import('../features/kit/UiKit'));
const RealtimeDemo = lazy(() => import('../features/realtime/RealtimeDemo'));

/**
 * What a route shows while its chunk loads: nothing at first, then — only if the load is slow — a
 * faint spinner. A fast load (the usual case) never flashes a loading state.
 */
function RouteFallback(): JSX.Element {
  return (
    <div className="flex h-full min-h-[40vh] items-center justify-center">
      <span
        className="h-4 w-4 rounded-full border-2 border-fg-faint/25 border-t-fg-faint opacity-0 [animation:spin_0.8s_linear_infinite,ff-fade_200ms_ease-out_250ms_forwards]"
        role="status"
        aria-label="Loading"
      />
    </div>
  );
}

function Lazy({ children }: { children: ReactNode }): JSX.Element {
  return <Suspense fallback={<RouteFallback />}>{children}</Suspense>;
}

export const routes: RouteObject[] = [
  {
    path: '/',
    element: (
      <Suspense fallback={null}>
        <Landing />
      </Suspense>
    ),
  },
  { path: '/login', element: <Login /> },
  { path: '/register', element: <Register /> },
  {
    path: '/_kit',
    element: (
      <Lazy>
        <UiKit />
      </Lazy>
    ),
  },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { path: '/dashboard', element: <Dashboard /> },
      {
        path: '/projects/:id',
        element: (
          <Lazy>
            <Workspace />
          </Lazy>
        ),
      },
      {
        path: '/eval',
        element: (
          <Lazy>
            <EvalDashboard />
          </Lazy>
        ),
      },
      {
        path: '/settings',
        element: (
          <Lazy>
            <Settings />
          </Lazy>
        ),
      },
      {
        path: '/admin/:section?',
        element: (
          <RequireAdmin>
            <Lazy>
              <AdminDashboard />
            </Lazy>
          </RequireAdmin>
        ),
      },
      {
        path: '/_realtime',
        element: (
          <Lazy>
            <RealtimeDemo />
          </Lazy>
        ),
      },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
