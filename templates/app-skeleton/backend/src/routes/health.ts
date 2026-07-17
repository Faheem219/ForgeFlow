import { Router } from 'express'

import { isDatabaseConnected } from '../db'

// Liveness endpoint. ForgeFlow's preview + deploy health checks probe GET /health, so it must
// answer 200 whenever the HTTP server is up — independent of database availability.
export const healthRouter = Router()

healthRouter.get('/', (_req, res) => {
  res.json({
    status: 'ok',
    db: isDatabaseConnected() ? 'connected' : 'disconnected',
    uptime: process.uptime(),
  })
})
