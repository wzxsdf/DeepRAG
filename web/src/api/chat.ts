import { client } from "./client"

export interface Conversation {
  id: string
  title: string
  messages: number
  updated_at: string
}

export interface Message {
  role: string
  content: string
  created_at: string
}

export interface StreamDone {
  answer: string
  citations: { chunk_id: string; document_id: string; page: number }[]
  grounded: boolean
  retry_count: number
  conversation_id: string
  source: string
}

export interface StepEvent {
  node: string
  title: string
  detail: string
}
export interface ThoughtEvent {
  tag: string
  query?: string
  found?: number
  snippets?: string[]
  name?: string
  args?: Record<string, unknown>
  result?: string
  steps?: { type: string; [k: string]: unknown }[]
  step_type?: string
  arg?: string
}

export const chatApi = {
  conversations: () => client.get<Conversation[]>("/conversations").then((r) => r.data),
  messages: (id: string) => client.get<Message[]>(`/conversations/${id}/messages`).then((r) => r.data),
}

/** SSE 流式问答: step(节点中间结果) + thought(Agent检索推理) + token(生成逐字) 回调 + done. */
export async function streamQuery(
  body: { question: string; conversation_id?: string; kb_ids?: string[]; mode?: string },
  onStep: (s: StepEvent) => void,
  onThought: (t: ThoughtEvent) => void,
  onToken: (tok: string) => void,
  signal?: AbortSignal
): Promise<StreamDone> {
  const token = localStorage.getItem("token")
  const resp = await fetch("/api/v1/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let done: StreamDone | null = null
  for (;;) {
    const { value, done: rdone } = await reader.read()
    if (rdone) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() || ""
    let evt = ""
    for (const line of lines) {
      if (line.startsWith("event:")) evt = line.slice(6).trim()
      else if (line.startsWith("data:")) {
        const data = line.slice(5).trim()
        try {
          if (evt === "step") onStep(JSON.parse(data))
          else if (evt === "thought") onThought(JSON.parse(data))
          else if (evt === "token") onToken(JSON.parse(data).text)
          else if (evt === "done") done = JSON.parse(data)
        } catch {
          /* ignore parse error */
        }
      }
    }
  }
  if (!done) throw new Error("无 done 事件")
  return done
}
