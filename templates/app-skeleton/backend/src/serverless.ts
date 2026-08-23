import express, { type Express } from 'express'

import { createApp } from './app'
import { config } from './config'
import { connectToDatabase } from './db'

// Serverless entrypoint (phase-58). Vercel's Node runtime imports a module and hands the exported
// Express app each request — there is no long-lived process and no port to bind, which is why
// src/index.ts (used by `pnpm dev`, `pnpm start` and the ForgeFlow preview) is not involved here.
//
// The connect step lives in a wrapper app rather than in createApp() so the app factory stays
// database-free for supertest, and so the preview path is byte-for-byte unchanged.
export function createServerlessApp(): Express {
  const app = express()

  app.use((_req, _res, next) => {
    connectToDatabase(config.mongoUri)
      .then(() => next())
      .catch((error: unknown) => {
        // Same posture as src/index.ts: a database failure is logged, not fatal, so /health still
        // answers and the app stays observable.
        // eslint-disable-next-line no-console
        console.error('MongoDB connection failed (request continuing):', error)
        next()
      })
  })

  app.use(createApp())

  return app
}

export default createServerlessApp()
