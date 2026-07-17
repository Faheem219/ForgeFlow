import type { NextFunction, Request, Response } from 'express'

// Minimal request logger: method, path, status, and latency. Swap for morgan/pino if a feature
// needs structured logs.
export function requestLogger(req: Request, res: Response, next: NextFunction): void {
  const start = Date.now()
  res.on('finish', () => {
    const ms = Date.now() - start
    // eslint-disable-next-line no-console
    console.log(`${req.method} ${req.originalUrl} ${res.statusCode} ${ms}ms`)
  })
  next()
}
