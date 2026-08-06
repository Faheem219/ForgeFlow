/* eslint-disable react-refresh/only-export-components */
import { Suspense, lazy } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router-dom';

import AdminDashboard from '../features/admin/AdminDashboard';
import Login from '../features/auth/Login';
import Register from '../features/auth/Register';
import { RequireAdmin, RequireAuth } from '../features/auth/guards';
import Dashboard from '../features/dashboard/Dashboard';
import EvalDashboard from '../features/eval/EvalDashboard';
import UiKit from '../features/kit/UiKit';
import RealtimeDemo from '../features/realtime/RealtimeDemo';
import Settings from '../features/settings/Settings';
import Workspace from '../features/workspace/Workspace';
import NotFound from './NotFound';
import { AppShell } from './layout/AppShell';

// The landing page carries GSAP + its scroll plugins — signed-in users going straight to the
// app never pay for it.
const Landing = lazy(() => import('../features/landing/Landing'));

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
  { path: '/_kit', element: <UiKit /> },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { path: '/dashboard', element: <Dashboard /> },
      { path: '/projects/:id', element: <Workspace /> },
      { path: '/eval', element: <EvalDashboard /> },
      { path: '/settings', element: <Settings /> },
      {
        path: '/admin/:section?',
        element: (
          <RequireAdmin>
            <AdminDashboard />
          </RequireAdmin>
        ),
      },
      { path: '/_realtime', element: <RealtimeDemo /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
