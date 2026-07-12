/* eslint-disable react-refresh/only-export-components */
import { createBrowserRouter, type RouteObject } from 'react-router-dom';

import Login from '../features/auth/Login';
import Register from '../features/auth/Register';
import { RequireAuth } from '../features/auth/guards';
import Dashboard from '../features/dashboard/Dashboard';
import UiKit from '../features/kit/UiKit';
import Settings from '../features/settings/Settings';
import Workspace from '../features/workspace/Workspace';
import NotFound from './NotFound';
import { AppShell } from './layout/AppShell';

export const routes: RouteObject[] = [
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
      { path: '/', element: <Dashboard /> },
      { path: '/projects/:id', element: <Workspace /> },
      { path: '/settings', element: <Settings /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
