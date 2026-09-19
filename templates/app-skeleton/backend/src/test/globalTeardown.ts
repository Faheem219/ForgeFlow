import { holder } from './globalSetup'

// Stops the run's in-memory MongoDB (phase-65) and removes its temporary data directory.
// ForgeFlow scaffold — do not rewrite.
export default async function globalTeardown(): Promise<void> {
  const server = holder.__forgeflowTestMongo
  holder.__forgeflowTestMongo = undefined
  if (server) {
    await server.stop()
  }
}
