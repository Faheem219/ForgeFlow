import mongoose from 'mongoose'

import { connectToDatabase } from './db'

// mongoose is mocked so this runs with no live database — it asserts the connect helper wires the
// injected URI through to mongoose.connect (the real connection is exercised in preview/CI).
jest.mock('mongoose', () => ({
  __esModule: true,
  default: {
    set: jest.fn(),
    connect: jest.fn().mockResolvedValue(undefined),
    connection: { readyState: 0 },
  },
}))

describe('connectToDatabase', () => {
  it('connects mongoose to the provided URI', async () => {
    const uri = 'mongodb://test-host:27017/testdb'
    await connectToDatabase(uri)

    expect(mongoose.set).toHaveBeenCalledWith('strictQuery', true)
    expect(mongoose.connect).toHaveBeenCalledWith(uri)
  })
})
