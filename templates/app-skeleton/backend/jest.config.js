/** @type {import('jest').Config} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: ['**/*.test.ts'],
  clearMocks: true,
  // One in-memory MongoDB per run, for tests that call useTestDb() (src/test/db.ts, phase-65).
  globalSetup: '<rootDir>/src/test/globalSetup.ts',
  globalTeardown: '<rootDir>/src/test/globalTeardown.ts',
  // Jest sizes its worker pool from the HOST's cores; the sandbox has 1 CPU and 1 GB, shared with
  // that mongod and the preview. Two workers keep parallelism without exhausting either.
  maxWorkers: 2,
}
