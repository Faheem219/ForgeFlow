import request from 'supertest'

import { createServerlessApp } from './serverless'

// Only the connect helper is replaced — the health route also imports isDatabaseConnected from this
// module, so stubbing the whole thing would take the route down with it.
jest.mock('./db', () => ({
  ...jest.requireActual<typeof import('./db')>('./db'),
  connectToDatabase: jest.fn().mockResolvedValue({}),
}))

const { connectToDatabase } = jest.requireMock<typeof import('./db')>('./db')
const connectMock = connectToDatabase as jest.MockedFunction<typeof connectToDatabase>

describe('serverless entrypoint', () => {
  it('serves the same app createApp() builds', async () => {
    const response = await request(createServerlessApp()).get('/health')

    expect(response.status).toBe(200)
  })

  it('ensures the database connection before handling a request', async () => {
    await request(createServerlessApp()).get('/health')

    expect(connectMock).toHaveBeenCalled()
  })

  // The preview process logs a DB failure and keeps serving; the serverless path must match, or a
  // cold start with a bad URI returns 500 for /health instead of staying observable.
  it('still serves when the database is unreachable', async () => {
    connectMock.mockRejectedValueOnce(new Error('unreachable'))
    jest.spyOn(console, 'error').mockImplementation(() => undefined)

    const response = await request(createServerlessApp()).get('/health')

    expect(response.status).toBe(200)
  })
})
