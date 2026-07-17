# `src/features/` — where backend feature code goes

Each feature is a self-contained folder. **The codegen agent writes here**; it never touches the
scaffold (app factory, config, db, middleware, health route).

```
src/features/<feature>/
  <feature>.model.ts        # Mongoose schema + model
  <feature>.schema.ts       # Zod request/response schemas (validation + shared types)
  <feature>.controller.ts   # request handlers (thin; call into services)
  <feature>.routes.ts       # express.Router() wiring, mounted in src/app.ts
  <feature>.test.ts         # Jest + supertest tests
```

Conventions:

- **Routing:** create an `express.Router()` and mount it in [`src/app.ts`](../app.ts) at the
  `FORGEFLOW:ROUTES` marker (e.g. `app.use('/api/todos', todosRouter)`).
- **Validation:** validate request bodies with Zod via
  [`validateBody`](../lib/validate.ts) — invalid input becomes a 400 automatically.
- **Errors:** throw [`AppError`](../middleware/errorHandler.ts) for expected client errors; the
  central handler returns a consistent `{ error }` envelope.
- **Data:** models use Mongoose against the injected `MONGODB_URI` (one isolated DB per project).
