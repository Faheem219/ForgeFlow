import mongoose from 'mongoose'

// Mongoose connection helpers. The URI is the per-project MONGODB_URI injected by ForgeFlow
// (phase-36 provisions an isolated database per project).
//
// The connection is parked on `globalThis` (phase-58). On a serverless platform the module is
// re-evaluated on a cold start but the Node process is reused across warm invocations, so without
// this every request would open a new socket and exhaust the cluster's connection limit. A
// long-lived process — `pnpm dev`, `pnpm start`, the ForgeFlow preview — takes the same path and
// simply connects once, so there is one code path rather than two.
interface ConnectionCache {
  conn: typeof mongoose | null
  promise: Promise<typeof mongoose> | null
}

const globalCache = globalThis as typeof globalThis & {
  __forgeflowMongoose?: ConnectionCache
}

const cache: ConnectionCache = (globalCache.__forgeflowMongoose ??= {
  conn: null,
  promise: null,
})

export async function connectToDatabase(uri: string): Promise<typeof mongoose> {
  if (cache.conn) {
    return cache.conn
  }
  if (!cache.promise) {
    mongoose.set('strictQuery', true)
    cache.promise = mongoose.connect(uri)
  }
  try {
    cache.conn = await cache.promise
  } catch (error) {
    // A failed attempt must not poison every later request: drop the promise so the next call
    // retries instead of re-awaiting a rejection forever.
    cache.promise = null
    throw error
  }
  return cache.conn
}

export async function disconnectFromDatabase(): Promise<void> {
  cache.conn = null
  cache.promise = null
  await mongoose.disconnect()
}

/** True when mongoose has an active connection (readyState 1). */
export function isDatabaseConnected(): boolean {
  return mongoose.connection.readyState === 1
}
