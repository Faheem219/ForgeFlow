// phase-65 smoke: mongodb-memory-server must start the mongod BAKED into the image, with no network.
//
// Run by run_smoke.sh under the real sandbox posture (`--network none`, read-only root, dropped caps)
// against a copy of the library installed into /workspace beforehand. If the image env ever stops
// pointing the library at /usr/local/bin/mongod it would try to download one — and, offline, fail.
'use strict'

const resolveFromWorkspace = (name) => require.resolve(name, { paths: ['/workspace'] })
const { MongoMemoryServer } = require(resolveFromWorkspace('mongodb-memory-server-core'))
const { MongoClient } = require(resolveFromWorkspace('mongodb'))

async function main() {
  const server = await MongoMemoryServer.create({
    instance: { args: ['--wiredTigerCacheSizeGB', '0.25'] },
  })
  const client = await MongoClient.connect(server.getUri())
  try {
    const notes = client.db('smoke').collection('notes')
    await notes.insertOne({ title: 'offline' })
    const found = await notes.findOne({ title: 'offline' })
    if (!found) {
      throw new Error('inserted document was not read back')
    }
  } finally {
    await client.close()
    await server.stop()
  }
  console.log('in-memory MongoDB OK (baked mongod, no network)')
}

main().catch((error) => {
  console.error('FAIL: mongodb-memory-server could not use the baked mongod offline:', error)
  process.exit(1)
})
