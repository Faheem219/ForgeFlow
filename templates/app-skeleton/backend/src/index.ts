import { createApp } from './app'
import { config } from './config'
import { connectToDatabase } from './db'

// Entry point. Start the HTTP server FIRST so /health is reachable immediately (the preview health
// check must pass even before the DB connects), then attempt the Mongo connection — a failure there
// is logged, not fatal, so the app stays observable.
async function main(): Promise<void> {
  const app = createApp()

  app.listen(config.port, '0.0.0.0', () => {
    // eslint-disable-next-line no-console
    console.log(`Server listening on http://0.0.0.0:${config.port} (${config.nodeEnv})`)
  })

  try {
    await connectToDatabase(config.mongoUri)
    // eslint-disable-next-line no-console
    console.log('Connected to MongoDB')
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error('MongoDB connection failed (server still running):', error)
  }
}

void main()
