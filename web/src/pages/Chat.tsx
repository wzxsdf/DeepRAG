import { useState, useRef, useEffect } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { toast } from "sonner"
import { Plus, Send, Sparkles, Loader2, Quote, CheckCircle2, AlertCircle, MessageSquare, RefreshCw, Brain, Search, ChevronDown, Compass, Filter, PenLine, ShieldCheck, Library, Wrench, GitBranch, Zap } from "lucide-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { chatApi, streamQuery, type StepEvent, type ThoughtEvent } from "@/api/chat"
import { kbApi } from "@/api/kb"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface Citation {
  chunk_id: string
  document_id: string
  page: number
}
interface Msg {
  role: "user" | "assistant"
  content: string
  citations?: Citation[]
  grounded?: boolean
  trace?: TraceItem[]
  streaming?: boolean
  retry_count?: number
  source?: string
}

// ponytail: 思考轨迹 = 节点中间结果(step) + Agent检索推理(thought), 按到达时序展示
type TraceItem =
  | ({ kind: "step" } & StepEvent)
  | ({ kind: "thought" } & ThoughtEvent)

// 节点→图标(标题由后端_step_detail给出, 前端只映射图标)
const STEP_ICON: Record<string, typeof Search> = {
  entry: Compass,
  retrieve: Search,
  grade: Filter,
  rewrite: RefreshCw,
  generate: PenLine,
  verify: ShieldCheck,
  tool_agent: Wrench,
  plan_agent: GitBranch,
}

const mapLast = (m: Msg[], fn: (last: Msg) => Msg): Msg[] =>
  m.length ? [...m.slice(0, -1), fn(m[m.length - 1])] : m

// ponytail: 来源徽章(4色) — kb_grounded绿/non_kb白/open_qa+fallback黄/refused红; 透明分层是vs通用LLM的差异化
const SOURCE_META: Record<string, { label: string; variant: "success" | "secondary" | "warning" | "destructive" | "info" | "planned"; icon: typeof CheckCircle2 }> = {
  kb_grounded: { label: "知识库·已引用", variant: "success", icon: CheckCircle2 },
  non_kb: { label: "非知识库", variant: "secondary", icon: MessageSquare },
  open_qa: { label: "常识·未验证", variant: "warning", icon: Brain },
  fallback_unverified: { label: "常识·未验证", variant: "warning", icon: AlertCircle },
  tool: { label: "工具·实时", variant: "info", icon: Wrench },
  planned: { label: "多源·编排", variant: "planned", icon: GitBranch },
  refused: { label: "无法回答", variant: "destructive", icon: AlertCircle },
}
const SUGGEST = [
  "DeepRAG的检索层用了什么技术?",
  "对比LangGraph和LlamaIndex",
  "BGE-M3输出多少维向量?",
]

type ChatMode = "quick" | "kb" | "agent"
const MODE_META: Record<ChatMode, { label: string; icon: typeof Search }> = {
  quick: { label: "快速问答", icon: Zap },
  kb: { label: "知识库", icon: Library },
  agent: { label: "智能体", icon: Sparkles },
}

export default function Chat() {
  const qc = useQueryClient()
  const [conv, setConv] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [openCite, setOpenCite] = useState<string | null>(null) // ponytail: 展开的引用键 `${msgIdx}-${citeIdx}`
  const [selectedKbs, setSelectedKbs] = useState<string[]>([]) // 选中的知识库(空=全部)
  const [kbOpen, setKbOpen] = useState(false)
  const [mode, setMode] = useState<ChatMode>(
    () => (localStorage.getItem("chat-mode") as ChatMode) || "quick"
  )
  const changeMode = (m: ChatMode) => {
    setMode(m)
    localStorage.setItem("chat-mode", m)
  }
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: conversations } = useQuery({ queryKey: ["convs"], queryFn: chatApi.conversations })
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: kbApi.list })

  const toggleKb = (id: string) => setSelectedKbs((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const loadConv = async (id: string) => {
    setConv(id)
    const msgs = await chatApi.messages(id)
    setMessages(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })))
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [messages])

  const send = async (q?: string) => {
    const question = q ?? input
    if (!question.trim() || streaming) return
    setInput("")
    setStreaming(true)
    setMessages((m) => [
      ...m,
      { role: "user", content: question },
      { role: "assistant", content: "", trace: [], streaming: true },
    ])
    try {
      const done = await streamQuery(
        { question, conversation_id: conv || undefined, kb_ids: selectedKbs.length ? selectedKbs : undefined, mode },
        (s) => setMessages((m) => mapLast(m, (last) => ({ ...last, trace: [...(last.trace || []), { kind: "step", ...s }] }))),
        (t) => setMessages((m) => mapLast(m, (last) => ({ ...last, trace: [...(last.trace || []), { kind: "thought", ...t }] }))),
        (tk) => setMessages((m) => mapLast(m, (last) => ({ ...last, content: (last.content || "") + tk }))),
      )
      setMessages((m) =>
        mapLast(m, (last) => ({
          ...last,
          content: done.answer,
          citations: done.citations,
          grounded: done.grounded,
          retry_count: done.retry_count,
          source: done.source,
          streaming: false,
        })),
      )
      if (!conv) setConv(done.conversation_id)
      qc.invalidateQueries({ queryKey: ["convs"] })
    } catch {
      toast.error("请求失败,请重试")
      setMessages((m) => m.slice(0, -2))
    }
    setStreaming(false)
  }

  return (
    <div className="flex h-full">
      {/* 会话侧栏 */}
      <div className="w-64 border-r bg-card flex flex-col">
        <div className="p-3 border-b">
          <Button
            variant="gradient"
            className="w-full"
            onClick={() => {
              setConv(null)
              setMessages([])
            }}
          >
            <Plus className="h-4 w-4" /> 新建对话
          </Button>
        </div>
        <div className="flex-1 overflow-auto p-2 space-y-1">
          {conversations && conversations.length === 0 && (
            <div className="text-center text-xs text-muted-foreground py-6 px-3">暂无历史对话</div>
          )}
          {conversations?.map((c) => (
            <button
              key={c.id}
              onClick={() => loadConv(c.id)}
              className={cn(
                "w-full text-left px-3 py-2 rounded-md text-sm truncate transition-colors flex items-center gap-2",
                conv === c.id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground"
              )}
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{c.title || "新对话"}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 消息区 */}
      <div className="flex-1 flex flex-col">
        <div ref={scrollRef} className="flex-1 overflow-auto p-6">
          {messages.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-center">
              <motion.div
                initial={{ scale: 0, rotate: -180 }}
                animate={{ scale: 1, rotate: 0 }}
                transition={{ type: "spring", duration: 0.8 }}
                className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 shadow-lg"
              >
                <Sparkles className="h-8 w-8 text-white" />
              </motion.div>
              <h2 className="text-2xl font-bold mb-2">DeepRAG 智能问答</h2>
              <p className="text-muted-foreground mb-6">基于 CRAG 纠正式检索的 Agentic RAG</p>
              <div className="flex flex-col gap-2 max-w-md">
                {SUGGEST.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="px-4 py-2.5 rounded-lg border text-left text-sm hover:bg-accent hover:border-primary/30 transition-all"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="max-w-3xl mx-auto space-y-4">
            <AnimatePresence>
              {messages.map((m, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}
                >
                  <div
                    className={cn(
                      "max-w-[80%] rounded-2xl px-4 py-3",
                      m.role === "user" ? "bg-primary text-primary-foreground" : "bg-card border shadow-sm"
                    )}
                  >
                    {m.role === "assistant" && (m.streaming || (m.trace?.length || 0) > 0) && (
                      <ThinkingTrace trace={m.trace || []} streaming={m.streaming} />
                    )}
                    {m.content && (
                      <div className="whitespace-pre-wrap leading-relaxed">
                        {m.content}
                        {m.streaming && (
                          <span className="inline-block w-1.5 h-3.5 bg-primary/70 animate-pulse align-text-bottom ml-0.5" />
                        )}
                      </div>
                    )}
                    {/* ponytail: 来源徽章(4色) — 历史消息无source则由grounded推断 */}
                    {m.role === "assistant" && !m.streaming && m.content &&
                      (() => {
                        const src = m.source || (m.grounded ? "kb_grounded" : "non_kb")
                        const meta = SOURCE_META[src] || SOURCE_META.non_kb
                        const Icon = meta.icon
                        return (
                          <div className="mt-2 flex items-center gap-2 flex-wrap">
                            <Badge variant={meta.variant} className="gap-1">
                              <Icon className="h-3 w-3" /> {meta.label}
                            </Badge>
                            {!!m.retry_count && m.retry_count > 0 && (
                              <Badge variant="secondary" className="gap-1">
                                <RefreshCw className="h-3 w-3" /> 自纠 {m.retry_count} 次
                              </Badge>
                            )}
                          </div>
                        )
                      })()}
                    {m.citations && m.citations.length > 0 && (
                      <div className="mt-3 pt-3 border-t space-y-2">
                        <div className="text-xs text-muted-foreground flex items-center gap-1.5 flex-wrap">
                          <Quote className="h-3 w-3" /> 来源(点击查看)
                          {m.citations.map((c, j) => {
                            const key = `${i}-${j}`
                            const active = openCite === key
                            return (
                              <button
                                key={j}
                                onClick={() => setOpenCite(active ? null : key)}
                                title={`第 ${c.page} 页 · ${c.document_id.slice(0, 8)}`}
                                className={cn(
                                  "inline-flex items-center rounded px-1.5 py-0.5 font-mono transition-colors",
                                  active ? "bg-primary text-primary-foreground" : "bg-accent hover:bg-primary/20"
                                )}
                              >
                                [{j + 1}]
                              </button>
                            )
                          })}
                        </div>
                        {(() => {
                          // ponytail: 当前消息展开的引用详情
                          const openJ = openCite && openCite.startsWith(`${i}-`) ? Number(openCite.split("-")[1]) : null
                          const oc = openJ !== null ? m.citations?.[openJ] : null
                          return (
                            <AnimatePresence>
                              {oc && (
                                <motion.div
                                  initial={{ opacity: 0, height: 0 }}
                                  animate={{ opacity: 1, height: "auto" }}
                                  exit={{ opacity: 0, height: 0 }}
                                  className="text-xs bg-accent/50 rounded px-2 py-1.5 overflow-hidden"
                                >
                                  来源 [{(openJ ?? 0) + 1}]: 第 {oc.page} 页 · 文档{" "}
                                  <span className="font-mono">{oc.document_id.slice(0, 8)}</span>
                                </motion.div>
                              )}
                            </AnimatePresence>
                          )
                        })()}
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </div>

        {/* 输入框 */}
        <div className="border-t p-4 bg-card/50 backdrop-blur">
          <div className="max-w-3xl mx-auto">
            {/* 模式选择 + 知识库多选 */}
            <div className="flex items-center gap-1 mb-2">
              {(Object.keys(MODE_META) as ChatMode[]).map((m) => {
                const M = MODE_META[m]
                const Icon = M.icon
                return (
                  <button
                    key={m}
                    onClick={() => changeMode(m)}
                    className={cn(
                      "inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs transition-colors",
                      mode === m ? "bg-primary text-primary-foreground" : "bg-accent text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Icon className="h-3 w-3" /> {M.label}
                  </button>
                )
              })}
              <div className="relative ml-auto">
                <button
                  onClick={() => setKbOpen((o) => !o)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Library className="h-3.5 w-3.5" />
                  {selectedKbs.length === 0 ? "全部知识库" : `${selectedKbs.length} 个知识库`}
                  <ChevronDown className={cn("h-3 w-3 transition-transform", kbOpen && "rotate-180")} />
                </button>
                <AnimatePresence>
                  {kbOpen && (
                    <motion.div
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 4 }}
                      className="absolute bottom-full mb-2 right-0 w-56 bg-card border rounded-lg shadow-lg p-1 z-10 max-h-72 overflow-auto"
                    >
                      <label className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent cursor-pointer text-sm">
                        <input type="checkbox" checked={selectedKbs.length === 0} onChange={() => setSelectedKbs([])} className="accent-primary" />
                        <span>全部知识库</span>
                      </label>
                      <div className="h-px bg-border my-1" />
                      {kbs?.map((kb) => (
                        <label key={kb.id} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent cursor-pointer text-sm">
                          <input type="checkbox" checked={selectedKbs.includes(kb.id)} onChange={() => toggleKb(kb.id)} className="accent-primary" />
                          <span className="truncate flex-1">{kb.name}</span>
                          <span className="text-xs text-muted-foreground shrink-0">{kb.doc_count}</span>
                        </label>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
            <div className="flex gap-2">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
                placeholder="输入问题, Enter 发送..."
                disabled={streaming}
              />
              <Button variant="gradient" size="icon" onClick={() => send()} disabled={streaming || !input.trim()}>
                {streaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// 可折叠"思考过程"面板: 节点中间结果(step) + Agent检索推理(thought), 按时序展开(豆包式)
function ThinkingTrace({ trace, streaming }: { trace: TraceItem[]; streaming?: boolean }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="mb-2 rounded-lg border bg-muted/40">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 w-full px-3 py-2 text-sm">
        {streaming ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-500" />
        ) : (
          <Brain className="h-3.5 w-3.5 text-indigo-500" />
        )}
        <span className="font-medium">思考过程</span>
        {trace.length > 0 && <span className="text-xs text-muted-foreground">{trace.length} 步</span>}
        <ChevronDown className={cn("h-3.5 w-3.5 ml-auto transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 space-y-1.5">
              {trace.length === 0 && streaming && (
                <div className="text-xs text-muted-foreground flex items-center gap-2">
                  <Loader2 className="h-3 w-3 animate-spin" /> 思考中...
                </div>
              )}
              {trace.map((t, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} className="text-xs">
                  {t.kind === "thought" ? (
                    t.tag === "plan" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <GitBranch className="h-3.5 w-3.5 mt-0.5 text-violet-500 shrink-0" />
                          <span className="text-muted-foreground">规划多源步骤：</span>
                        </div>
                        <div className="ml-5 pl-2 border-l border-violet-300 space-y-0.5">
                          {(t.steps || []).map((s, j) => (
                            <div key={j} className="text-muted-foreground/80">
                              › {s.type}: {Object.entries(s).filter(([k]) => k !== "type").map(([, v]) => String(v)).join(" ")}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : t.tag === "plan_exec" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <GitBranch className="h-3.5 w-3.5 mt-0.5 text-violet-500 shrink-0" />
                          <span className="text-muted-foreground">
                            执行 <span className="text-foreground font-medium">{t.step_type}</span>（{t.arg}）
                          </span>
                        </div>
                        {t.result && (
                          <div className="ml-5 pl-2 border-l border-border">
                            <div className="text-muted-foreground/70 leading-relaxed break-all">› {t.result}</div>
                          </div>
                        )}
                      </div>
                    ) : t.tag === "tool_call" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <Wrench className="h-3.5 w-3.5 mt-0.5 text-sky-500 shrink-0" />
                          <span className="text-muted-foreground">
                            调用工具「<span className="text-foreground font-medium">{t.name}</span>」
                            {t.args && Object.keys(t.args).length > 0 && (
                              <>（{Object.entries(t.args).map(([k, v]) => `${k}: ${String(v)}`).join("，")}）</>
                            )}
                          </span>
                        </div>
                        {t.result && (
                          <div className="ml-5 pl-2 border-l border-border">
                            <div className="text-muted-foreground/70 leading-relaxed break-all">› {t.result}</div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <Search className="h-3.5 w-3.5 mt-0.5 text-indigo-500 shrink-0" />
                          <span className="text-muted-foreground">
                            搜索「<span className="text-foreground font-medium">{t.query}</span>」→ 找到 {t.found} 条
                          </span>
                        </div>
                        {t.snippets && t.snippets.length > 0 && (
                          <div className="ml-5 pl-2 border-l border-border space-y-0.5">
                            {t.snippets.map((s, j) => (
                              <div key={j} className="text-muted-foreground/70 leading-relaxed break-all">› {s}</div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  ) : (
                    <div className="flex items-start gap-2">
                      {(() => {
                        const Icon = STEP_ICON[t.node] || CheckCircle2
                        return <Icon className="h-3.5 w-3.5 mt-0.5 text-emerald-500 shrink-0" />
                      })()}
                      <span>
                        <span className="font-medium">{t.title}</span>
                        {t.detail && <span className="text-muted-foreground"> · {t.detail}</span>}
                      </span>
                    </div>
                  )}
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
