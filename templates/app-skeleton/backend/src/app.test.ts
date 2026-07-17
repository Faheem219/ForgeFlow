import request from 'supertest'

import { createApp } from './app'

// Example unit tests (Jest + supertest). They exercise the app without a live DB — proving the
// toolchain works and that /health is DB-independent. Generated feature tests slot in the same way.
describe('app', () => {
  it('GET /health returns 200 with status ok', async () => {
    const app = createApp()
    const res = await request(app).get('/health')

    expect(res.status).toBe(200)
    expect(res.body.status).toBe('ok')
  })

  it('unknown routes return a 404 error envelope', async () => {
    const app = createApp()
    const res = await request(app).get('/does-not-exist')

    expect(res.status).toBe(404)
    expect(res.body.error).toBeDefined()
  })
})
