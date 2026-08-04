import { client } from "./client"

export interface Metrics {
  jobs: Record<string, number>
  documents: number
  chunks: number
  conversations: number
  messages: number
  dependencies: Record<string, string>
}

export const monitorApi = {
  metrics: () => client.get<Metrics>("/metrics").then((r) => r.data),
}
