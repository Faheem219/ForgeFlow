import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { registerSW } from 'virtual:pwa-register';

// Self-hosted fonts (no CDN): Inter for UI, JetBrains Mono for code/terminal surfaces.
import '@fontsource-variable/inter';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';

import { Providers } from './app/providers';
import { router } from './app/router';
import { registerPwa } from './pwa/registerPwa';
import './app/theme/index.css';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  </React.StrictMode>,
);

registerPwa((options) => registerSW(options));
