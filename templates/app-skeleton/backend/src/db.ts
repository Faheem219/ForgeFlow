import mongoose from 'mongoose'

// Mongoose connection helpers. The URI is the per-project MONGODB_URI injected by ForgeFlow
// (phase-36 provisions an isolated database per project).
export async function connectToDatabase(uri: string): Promise<typeof mongoose> {
  mongoose.set('strictQuery', true)
  return mongoose.connect(uri)
}

export async function disconnectFromDatabase(): Promise<void> {
  await mongoose.disconnect()
}

/** True when mongoose has an active connection (readyState 1). */
export function isDatabaseConnected(): boolean {
  return mongoose.connection.readyState === 1
}
