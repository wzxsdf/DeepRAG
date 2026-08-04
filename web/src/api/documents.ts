import { client } from "./client"

export interface DocItem {
  id: string
  title: string
  status: string
  stage: string
  created_at: string
  kb_id: string
}

export interface PresignResp {
  upload_url: string
  oss_key: string
  headers: Record<string, string>
}

export const docApi = {
  list: (kb_id?: string) =>
    client.get<DocItem[]>("/documents", { params: kb_id ? { kb_id } : {} }).then((r) => r.data),
  get: (id: string) => client.get(`/documents/${id}`).then((r) => r.data),
  preview: (id: string) => client.get<{ url: string }>(`/documents/${id}/preview`).then((r) => r.data),
  remove: (id: string) => client.delete(`/documents/${id}`).then((r) => r.data),
  presign: (filename: string, file_size: number) =>
    client.post<PresignResp>("/uploads/presign", { filename, file_size }).then((r) => r.data),
  confirm: (data: { oss_key: string; title: string; file_size: number; sha256: string; kb_id: string }) =>
    client.post("/documents/confirm", data).then((r) => r.data),
}

export async function sha256(file: File): Promise<string> {
  const buf = await file.arrayBuffer()
  const hash = await crypto.subtle.digest("SHA-256", buf)
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
}
