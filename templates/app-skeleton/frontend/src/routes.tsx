import { createBrowserRouter } from 'react-router-dom'

import { Layout } from './components/Layout'
import { HomePage } from './pages/HomePage'

// The route table. The codegen agent registers new feature routes here as children of Layout.
// Keep this as the single source of truth for navigation.
export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      // FORGEFLOW:ROUTES — the agent adds feature routes here.
    ],
  },
])
