import { client } from "./client"

export interface KbItem {
  id: string
  name: string
  doc_count: number
  created_at: string
}

export const kbApi = {
  list: () => client.get<KbItem[]>("/kbs").then((r) => r.data),
  create: (name: string) => client.post<{ id: string; name: string }>("/kbs", { name }).then((r) => r.data),
  rename: (id: string, name: string) => client.patch<{ id: string; name: string }>(`/kbs/${id}`, { name }).then((r) => r.data),
  remove: (id: string) => client.delete(`/kbs/${id}`).then((r) => r.data),
}
