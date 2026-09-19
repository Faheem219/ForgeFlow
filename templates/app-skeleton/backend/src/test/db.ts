import { randomUUID } from 'node:crypto'

import mongoose from 'mongoose'

// Test database helper (phase-65). ForgeFlow scaffold — do not rewrite.
//
// Backend tests that touch data use a REAL MongoDB: the in-memory server jest.config.js starts once
// per run (./globalSetup.ts). Call `useTestDb()` once at the top of a test file:
//
//   import { useTestDb } from '../../test/db'
//   useTestDb()
//
// Each file gets its own database, every collection is emptied between tests, and the database is
// dropped when the file finishes — so files never see each other's data and need no cleanup code.

/** Stable marker in the error thrown when there is no test database, so tooling can recognise it. */
export const TEST_DB_UNAVAILABLE = '[forgeflow:test-db-unavailable]'

function serverUri(): string {
  const reason = process.env.FORGEFLOW_TEST_DB_ERROR
  if (reason) {
    throw new Error(`${TEST_DB_UNAVAILABLE} The in-memory MongoDB could not start: ${reason}`)
  }
  const uri = process.env.FORGEFLOW_TEST_MONGODB_URI
  if (!uri) {
    throw new Error(
      `${TEST_DB_UNAVAILABLE} No in-memory MongoDB is running: backend/jest.config.js must keep ` +
        'its globalSetup (src/test/globalSetup.ts).',
    )
  }
  return uri
}

/** Connects mongoose to a fresh database of its own on the run's in-memory server. */
export async function connectTestDb(): Promise<typeof mongoose> {
  const dbName = `test_${process.env.JEST_WORKER_ID ?? '0'}_${randomUUID().slice(0, 8)}`
  return mongoose.connect(serverUri(), { dbName })
}

/** Empties every collection, keeping indexes — the state each test starts from. */
export async function clearTestDb(): Promise<void> {
  // Not connected means connect already failed with the real reason; clearing would only add
  // mongoose's 10s buffering timeout per test on top of it.
  if (mongoose.connection.readyState !== 1) {
    return
  }
  const collections = Object.values(mongoose.connection.collections)
  await Promise.all(collections.map((collection) => collection.deleteMany({})))
}

/** Drops this file's database and disconnects. */
export async function disconnectTestDb(): Promise<void> {
  if (mongoose.connection.readyState === 1) {
    await mongoose.connection.dropDatabase()
  }
  await mongoose.disconnect()
}

/** Registers connect / clear-between-tests / drop hooks for the current test file. */
export function useTestDb(): void {
  beforeAll(async () => {
    await connectTestDb()
  })
  afterEach(async () => {
    await clearTestDb()
  })
  afterAll(async () => {
    await disconnectTestDb()
  })
}
