import { MongoMemoryServer } from 'mongodb-memory-server-core'

// One in-memory MongoDB per Jest run (phase-65). ForgeFlow scaffold — do not rewrite.
//
// Inside the ForgeFlow sandbox the server is the `mongod` baked into the image (MONGOMS_SYSTEM_BINARY):
// there is no network while tests run, so nothing is ever downloaded. Elsewhere (a laptop, CI) the
// library fetches a binary on first use, as usual.
//
// The URI is published as MONGODB_URI too, so whatever launched Jest — the agent, the Test stage, a
// developer — every test that reads the app's config lands on this throwaway server and never on a
// real database. Each test file then gets its own database on it (see ./db.ts).

type ServerHolder = typeof globalThis & { __forgeflowTestMongo?: MongoMemoryServer }

export const holder = globalThis as ServerHolder

export default async function globalSetup(): Promise<void> {
  try {
    const server = await MongoMemoryServer.create({
      // Sized for the sandbox: 1 GB of memory shared with Jest's workers and the preview.
      instance: { args: ['--wiredTigerCacheSizeGB', '0.25'] },
    })
    holder.__forgeflowTestMongo = server
    process.env.FORGEFLOW_TEST_MONGODB_URI = server.getUri()
    process.env.MONGODB_URI = server.getUri()
  } catch (error) {
    // Fail soft: suites that never touch the database still run. The ones that do fail fast, with
    // this reason, from useTestDb() — instead of hanging on mongoose's 10s buffering timeout.
    const reason = error instanceof Error ? error.message : String(error)
    process.env.FORGEFLOW_TEST_DB_ERROR = reason
    // eslint-disable-next-line no-console
    console.warn(`[forgeflow] in-memory MongoDB unavailable; database tests will fail: ${reason}`)
  }
}
