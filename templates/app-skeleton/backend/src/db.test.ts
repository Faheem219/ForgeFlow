import mongoose from 'mongoose'

import { connectToDatabase, disconnectFromDatabase } from './db'

// mongoose is mocked so this runs with no live database — it asserts the connect helper wires the
// injected URI through to mongoose.connect (the real connection is exercised in preview/CI).
jest.mock('mongoose', () => ({
  __esModule: true,
  default: {
    set: jest.fn(),
    connect: jest.fn().mockResolvedValue({ connected: true }),
    disconnect: jest.fn().mockResolvedValue(undefined),
    connection: { readyState: 0 },
  },
}))

// The connection cache is module state, so each test starts from a disconnected cache.
beforeEach(async () => {
  await disconnectFromDatabase()
  jest.clearAllMocks()
})

describe('connectToDatabase', () => {
  it('connects mongoose to the provided URI', async () => {
    const uri = 'mongodb://test-host:27017/testdb'
    await connectToDatabase(uri)

    expect(mongoose.set).toHaveBeenCalledWith('strictQuery', true)
    expect(mongoose.connect).toHaveBeenCalledWith(uri)
  })

  // phase-58: on a serverless platform the module is reused across warm invocations. Opening a new
  // socket per request is what exhausts a cluster's connection limit, so the second call must reuse
  // the first connection rather than dial again.
  it('opens exactly one connection across repeated calls', async () => {
    const uri = 'mongodb://test-host:27017/testdb'

    const first = await connectToDatabase(uri)
    const second = await connectToDatabase(uri)

    expect(mongoose.connect).toHaveBeenCalledTimes(1)
    expect(second).toBe(first)
  })

  // A rejected attempt must not be cached, or every later request re-awaits the same rejection.
  it('retries after a failed connection instead of caching the rejection', async () => {
    const uri = 'mongodb://test-host:27017/testdb'
    const connect = mongoose.connect as jest.Mock
    connect.mockRejectedValueOnce(new Error('unreachable'))

    await expect(connectToDatabase(uri)).rejects.toThrow('unreachable')
    await connectToDatabase(uri)

    expect(connect).toHaveBeenCalledTimes(2)
  })
})
