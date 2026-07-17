import cors from 'cors'
import express, { type Express } from 'express'

import { errorHandler, notFound } from './middleware/errorHandler'
import { requestLogger } from './middleware/requestLogger'
import { healthRouter } from './routes/health'

// Express app factory. Kept separate from index.ts so tests (supertest) can exercise the app
// without opening a port or requiring a live database.
export function createApp(): Express {
  const app = express()

  app.use(cors())
  app.use(express.json())
  app.use(requestLogger)

  app.use('/health', healthRouter)

  // FORGEFLOW:ROUTES — the agent mounts feature routers here, e.g.
  //   app.use('/api/todos', todosRouter)

  app.use(notFound)
  app.use(errorHandler)

  return app
}
