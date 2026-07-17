import type { ErrorRequestHandler, RequestHandler } from 'express'
import { ZodError } from 'zod'

// Throw this from feature code for expected, client-facing failures (e.g. 404s, validation).
export class AppError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'AppError'
    this.status = status
  }
}

// 404 handler — mounted after all routes. Produces the same `{ error }` envelope as errors.
export const notFound: RequestHandler = (req, res) => {
  res.status(404).json({ error: `Not found: ${req.method} ${req.path}` })
}

// Centralized error middleware (Express recognizes it by its 4-arg signature). Maps known error
// types to status codes and hides internals behind a generic 500.
export const errorHandler: ErrorRequestHandler = (err, _req, res, _next) => {
  if (err instanceof AppError) {
    res.status(err.status).json({ error: err.message })
    return
  }
  if (err instanceof ZodError) {
    res.status(400).json({ error: 'Validation failed', issues: err.issues })
    return
  }
  // eslint-disable-next-line no-console
  console.error('Unhandled error:', err)
  res.status(500).json({ error: 'Internal server error' })
}
