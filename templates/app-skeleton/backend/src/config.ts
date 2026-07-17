// Centralized config. Reads the injected env contract (see .env.example). Everything the app needs
// from the environment is resolved here once, so feature code imports typed values, not process.env.

export interface AppConfig {
  port: number
  mongoUri: string
  nodeEnv: string
}

export function loadConfig(): AppConfig {
  return {
    port: Number(process.env.PORT ?? 3001),
    mongoUri: process.env.MONGODB_URI ?? 'mongodb://localhost:27017/forgeflow_app',
    nodeEnv: process.env.NODE_ENV ?? 'development',
  }
}

export const config = loadConfig()
