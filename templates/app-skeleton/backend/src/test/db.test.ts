import mongoose, { Schema } from 'mongoose'

import { useTestDb } from './db'

// Proves the offline test database end to end (phase-65): a real Mongoose round-trip against the
// run's in-memory MongoDB. ForgeFlow scaffold — feature tests use `useTestDb()` the same way.
useTestDb()

const Note = mongoose.model('ScaffoldNote', new Schema({ title: { type: String, required: true } }))

describe('test database', () => {
  it('persists a document and reads it back', async () => {
    await Note.create({ title: 'hello' })

    const found = await Note.findOne({ title: 'hello' }).lean()

    expect(found?.title).toBe('hello')
  })

  it('starts every test from empty collections', async () => {
    expect(await Note.countDocuments()).toBe(0)
  })

  it('gives each test file a database of its own', () => {
    expect(mongoose.connection.name).toMatch(/^test_/)
  })
})
