import type { NextFunction, Request, Response } from 'express'
import type { ZodTypeAny } from 'zod'

// Zod request-body validation middleware. On failure it forwards the ZodError to the central error
// handler (→ 400). On success it replaces req.body with the parsed, typed value.
//
//   router.post('/todos', validateBody(createTodoSchema), createTodo)
export function validateBody(schema: ZodTypeAny) {
  return (req: Request, _res: Response, next: NextFunction): void => {
    const result = schema.safeParse(req.body)
    if (!result.success) {
      next(result.error)
      return
    }
    req.body = result.data
    next()
  }
}
