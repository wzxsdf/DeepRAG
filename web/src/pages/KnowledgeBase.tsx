import { useState, type DragEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { motion, AnimatePresence } from "framer-motion"
import { toast } from "sonner"
import { Upload, FileText, Loader2, CheckCircle2, XCircle, Clock, ChevronDown, ChevronLeft, Plus, BookOpen, Eye, ExternalLink, X, Trash2, Pencil, AlertTriangle } from "lucide-react"
import { docApi, sha256, type DocItem } from "@/api/documents"
import { kbApi, type KbItem } from "@/api/kb"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

function StatusBadge({ status }: { status: string }) {
  const s = status.toUpperCase()
  if (["READY", "INDEXED", "DONE"].includes(s)) return <Badge variant="success" className="gap-1"><CheckCircle2 className="h-3 w-3" />{s}</Badge>
  if (["FAILED", "ERROR"].includes(s)) return <Badge variant="destructive" className="gap-1"><XCircle className="h-3 w-3" />{s}</Badge>
  if (["PENDING", "UPLOADED", "PARSING", "INDEXING"].includes(s)) return <Badge variant="warning" className="gap-1"><Clock className="h-3 w-3" />{s}</Badge>
  return <Badge variant="secondary">{s}</Badge>
}

// ponytail: 相对时间(3天前/2小时前)比绝对时间戳更友好
const timeAgo = (s: string) => {
  const diff = Date.now() - new Date(s).getTime()
  const m = Math.floor(diff / 60000), h = Math.floor(diff / 3600000), d = Math.floor(diff / 86400000)
  if (d > 0) return `${d} 天前`
  if (h > 0) return `${h} 小时前`
  if (m > 0) return `${m} 分钟前`
  return "刚刚"
}

// ponytail: 文档图标按状态着色(成功绿/失败红/进行中橙), 一眼区分入库状态
const docIconColor = (status: string) => {
  const s = status.toUpperCase()
  if (["INDEXED", "READY", "DONE"].includes(s)) return "text-emerald-500"
  if (["FAILED", "ERROR"].includes(s)) return "text-red-500"
  return "text-amber-500"
}
const docIconBg = (status: string) => {
  const s = status.toUpperCase()
  if (["INDEXED", "READY", "DONE"].includes(s)) return "bg-emerald-500/10"
  if (["FAILED", "ERROR"].includes(s)) return "bg-red-500/10"
  return "bg-amber-500/10"
}

export default function KnowledgeBase() {
  const qc = useQueryClient()
  const [selectedKb, setSelectedKb] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [detail, setDetail] = useState<Record<string, any>>({})
  const [newKbName, setNewKbName] = useState("")
  const [creating, setCreating] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [renamingKb, setRenamingKb] = useState<KbItem | null>(null)
  const [deletingKb, setDeletingKb] = useState<KbItem | null>(null)
  const [renameInput, setRenameInput] = useState("")
  const [deleteInput, setDeleteInput] = useState("")
  const [deletingDoc, setDeletingDoc] = useState<DocItem | null>(null)

  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: kbApi.list })
  const { data: docs, refetch } = useQuery({
    queryKey: ["docs", selectedKb],
    queryFn: () => docApi.list(selectedKb || undefined),
    enabled: !!selectedKb,
    refetchInterval: (q) => {
      const data = q.state.data as DocItem[] | undefined
      return data?.some((d) => ["PENDING", "PARSING", "INDEXING", "UPLOADED"].includes(d.status.toUpperCase())) ? 3000 : false
    },
  })

  const currentKb = kbs?.find((k) => k.id === selectedKb)

  const createKb = async () => {
    if (!newKbName.trim()) return
    setCreating(true)
    try {
      await kbApi.create(newKbName.trim())
      setNewKbName("")
      qc.invalidateQueries({ queryKey: ["kbs"] })
      toast.success("知识库已创建")
    } catch {
      toast.error("创建失败")
    }
    setCreating(false)
  }

  const handleFile = async (file: File) => {
    if (!selectedKb) {
      toast.error("请先选择知识库")
      return
    }
    setUploading(true)
    try {
      const presign = await docApi.presign(file.name, file.size)
      // ponytail: body用ArrayBuffer而非File——浏览器对File body自动加Content-Type(=file.type),
      // 而presigned URL签名不含CT, OSS验签纳入CT→SignatureDoesNotMatch 403. ArrayBuffer不带CT, 验签通过
      const putResp = await fetch(presign.upload_url, { method: "PUT", body: await file.arrayBuffer(), headers: presign.headers })
      if (!putResp.ok) throw new Error(`OSS上传失败(HTTP ${putResp.status})`)
      const hash = await sha256(file)
      await docApi.confirm({ oss_key: presign.oss_key, title: file.name, file_size: file.size, sha256: hash, kb_id: selectedKb })
      toast.success("上传成功,正在入库...")
      qc.invalidateQueries({ queryKey: ["docs", selectedKb] })
      qc.invalidateQueries({ queryKey: ["kbs"] })
    } catch (err) {
      const msg = (err as Error).message
      toast.error(msg?.includes("Failed to fetch") ? "上传失败(OSS可能需配CORS)" : msg || "上传失败")
    }
    setUploading(false)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const toggleDetail = async (id: string) => {
    if (detail[id]) {
      setDetail((d) => ({ ...d, [id]: null }))
      return
    }
    try {
      setDetail((d) => ({ ...d, [id]: "loading" }))
      const r = await docApi.get(id)
      setDetail((d) => ({ ...d, [id]: r }))
    } catch {
      setDetail((d) => ({ ...d, [id]: null }))
    }
  }

  const openPreview = async (id: string) => {
    try {
      const { url } = await docApi.preview(id)
      setPreviewUrl(url)
    } catch {
      toast.error("预览加载失败")
    }
  }

  const doRename = async () => {
    if (!renamingKb || !renameInput.trim()) return
    try {
      await kbApi.rename(renamingKb.id, renameInput.trim())
      qc.invalidateQueries({ queryKey: ["kbs"] })
      setRenamingKb(null)
      toast.success("已重命名")
    } catch {
      toast.error("重命名失败")
    }
  }

  const doDelete = async () => {
    // ponytail: 输入名匹配才删(企业级防误删); 调后端DELETE级联清理Qdrant/OSS/MySQL
    if (!deletingKb || deleteInput !== deletingKb.name) return
    try {
      await kbApi.remove(deletingKb.id)
      qc.invalidateQueries({ queryKey: ["kbs"] })
      qc.invalidateQueries({ queryKey: ["docs"] })
      setDeletingKb(null)
      toast.success("知识库已删除")
    } catch {
      toast.error("删除失败")
    }
  }

  const doDeleteDoc = async () => {
    // ponytail: 删单个文档(后端DELETE级联清Qdrant/OSS/MySQL子表); 确认弹窗触发
    if (!deletingDoc) return
    try {
      await docApi.remove(deletingDoc.id)
      qc.invalidateQueries({ queryKey: ["docs", selectedKb] })
      qc.invalidateQueries({ queryKey: ["kbs"] })
      setDeletingDoc(null)
      toast.success("文档已删除")
    } catch {
      toast.error("删除失败")
    }
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-5xl mx-auto">
        <button
          onClick={() => setSelectedKb(null)}
          className={cn("flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4", !selectedKb && "invisible")}
        >
          <ChevronLeft className="h-4 w-4" /> 返回知识库列表
        </button>
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BookOpen className="h-6 w-6 text-indigo-500" />
            {currentKb?.name || "知识库"}
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            {selectedKb ? "上传文档到此知识库,自动解析入库" : "按主题组织文档,对话时可选择检索范围"}
          </p>
        </motion.div>

        {!selectedKb ? (
          /* ---- KB 列表视图 ---- */
          <>
            <Card className="mb-6">
              <CardContent className="p-4 flex gap-2">
                <Input
                  value={newKbName}
                  onChange={(e) => setNewKbName(e.target.value)}
                  placeholder="新知识库名称(如:产品手册 / 论文集)"
                  onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && createKb()}
                />
                <Button variant="gradient" onClick={createKb} disabled={creating || !newKbName.trim()}>
                  {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} 创建
                </Button>
              </CardContent>
            </Card>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {kbs?.map((kb, i) => (
                <motion.div key={kb.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
                  <Card className="group hover:shadow-lg hover:border-primary/40 hover:-translate-y-1 transition-all cursor-pointer h-full overflow-hidden" onClick={() => setSelectedKb(kb.id)}>
                    {/* ponytail: 顶部渐变条, 悬停加亮, 卡片层次感 */}
                    <div className="h-1 bg-gradient-to-r from-indigo-500 to-violet-500 opacity-50 group-hover:opacity-100 transition-opacity" />
                    <CardContent className="p-5">
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 shadow-sm shadow-indigo-500/20">
                          <BookOpen className="h-5 w-5 text-white" />
                        </div>
                        <div className="flex items-center gap-0.5">
                          <Badge variant="secondary" className="mr-1">{kb.doc_count} 文档</Badge>
                          {/* ponytail: 管理按钮默认隐藏, group-hover显(视觉干净); stopPropagation防触发进入 */}
                          <button
                            onClick={(e) => { e.stopPropagation(); setRenamingKb(kb); setRenameInput(kb.name) }}
                            className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-accent text-muted-foreground hover:text-foreground transition-all"
                            title="重命名"
                          ><Pencil className="h-3.5 w-3.5" /></button>
                          <button
                            onClick={(e) => { e.stopPropagation(); setDeletingKb(kb); setDeleteInput("") }}
                            className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-all"
                            title="删除"
                          ><Trash2 className="h-3.5 w-3.5" /></button>
                        </div>
                      </div>
                      <div className="font-semibold truncate">{kb.name}</div>
                      <div className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                        <Clock className="h-3 w-3" />{timeAgo(kb.created_at)}
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              ))}
              {kbs && kbs.length === 0 && (
                <div className="col-span-full text-center py-16 flex flex-col items-center gap-3">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent">
                    <BookOpen className="h-7 w-7 text-muted-foreground" />
                  </div>
                  <div className="text-sm text-muted-foreground">暂无知识库,在上方创建第一个</div>
                </div>
              )}
            </div>
          </>
        ) : (
          /* ---- KB 详情视图 ---- */
          <>
            <label
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={cn("block mb-6 cursor-pointer", uploading && "pointer-events-none")}
            >
              <Card className={cn("border-2 border-dashed transition-colors", dragging ? "border-primary bg-primary/5" : "border-border")}>
                <CardContent className="flex flex-col items-center justify-center py-10 text-center">
                  <motion.div animate={dragging ? { scale: 1.1 } : { scale: 1 }} className="mb-3 flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 shadow-md">
                    {uploading ? <Loader2 className="h-7 w-7 text-white animate-spin" /> : <Upload className="h-7 w-7 text-white" />}
                  </motion.div>
                  <div className="font-medium">{uploading ? "上传中..." : dragging ? "松开上传" : "拖拽 PDF 到此处,或点击选择"}</div>
                  <div className="text-xs text-muted-foreground mt-1">支持 PDF, 自动 Parent-Child 切分 + BGE-M3 向量化</div>
                  <input type="file" accept=".pdf,application/pdf" className="hidden" onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])} />
                </CardContent>
              </Card>
            </label>

            <Card>
              <CardContent className="p-0">
                <div className="flex items-center justify-between p-4 border-b">
                  <div className="font-semibold">文档列表 ({docs?.length || 0})</div>
                  <Button variant="ghost" size="sm" onClick={() => refetch()}>
                    <Loader2 className="h-4 w-4 mr-1" /> 刷新
                  </Button>
                </div>
                <div className="divide-y">
                  {docs?.length === 0 && (
                    <div className="p-12 text-center text-muted-foreground text-sm flex flex-col items-center gap-2">
                      <FileText className="h-8 w-8 opacity-40" />
                      此知识库暂无文档,上传第一个吧
                    </div>
                  )}
                  {docs?.map((d, i) => (
                    <div key={d.id}>
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: i * 0.03 }}
                        onClick={() => toggleDetail(d.id)}
                        className="group flex items-center gap-3 p-4 hover:bg-accent/50 transition-colors cursor-pointer"
                      >
                        <div className={cn("flex h-9 w-9 items-center justify-center rounded-md shrink-0", docIconBg(d.status))}>
                          <FileText className={cn("h-4 w-4", docIconColor(d.status))} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium truncate">{d.title}</div>
                          <div className="text-xs text-muted-foreground flex items-center gap-1">
                            <Clock className="h-3 w-3" />{timeAgo(d.created_at)}
                          </div>
                        </div>
                        <StatusBadge status={d.status} />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => { e.stopPropagation(); openPreview(d.id) }}
                          className="opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <Eye className="h-3.5 w-3.5" /> 预览
                        </Button>
                        {/* ponytail: 文档删除(hover显, stopPropagation), 调DELETE级联清Qdrant/OSS/MySQL */}
                        <button
                          onClick={(e) => { e.stopPropagation(); setDeletingDoc(d) }}
                          className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-all"
                          title="删除文档"
                        ><Trash2 className="h-3.5 w-3.5" /></button>
                        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform shrink-0", detail[d.id] && "rotate-180")} />
                      </motion.div>
                      <AnimatePresence>
                        {detail[d.id] && detail[d.id] !== "loading" && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            className="px-4 pb-4 pl-16 text-xs space-y-1 overflow-hidden"
                          >
                            <div className="text-muted-foreground">阶段: <span className="font-mono">{detail[d.id].stage || "-"}</span></div>
                            <div className="text-muted-foreground">版本: <span className="font-mono">{detail[d.id].version_id?.slice(0, 8) || "-"}</span></div>
                            {detail[d.id].error_msg && (
                              <div className="text-destructive break-all">错误: {detail[d.id].error_msg}</div>
                            )}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* 文档预览弹窗 */}
      <AnimatePresence>
        {previewUrl && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
            onClick={() => setPreviewUrl(null)}
          >
            <motion.div
              initial={{ scale: 0.95 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.95 }}
              className="bg-card rounded-xl shadow-xl w-full max-w-4xl h-[85vh] flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between p-3 border-b">
                <span className="font-medium flex items-center gap-2"><FileText className="h-4 w-4" /> 文档预览</span>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="sm" onClick={() => window.open(previewUrl, "_blank")}>
                    <ExternalLink className="h-3.5 w-3.5" /> 新标签打开
                  </Button>
                  <Button variant="ghost" size="icon" onClick={() => setPreviewUrl(null)}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <iframe src={previewUrl} className="flex-1 w-full rounded-b-xl" title="文档预览" />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 重命名弹窗 */}
      <AnimatePresence>
        {renamingKb && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
            onClick={() => setRenamingKb(null)}>
            <motion.div initial={{ scale: 0.95 }} animate={{ scale: 1 }} exit={{ scale: 0.95 }}
              className="bg-card rounded-xl shadow-xl w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
              <div className="font-semibold mb-3 flex items-center gap-2"><Pencil className="h-4 w-4" /> 重命名知识库</div>
              <Input value={renameInput} onChange={(e) => setRenameInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doRename()} autoFocus />
              <div className="flex gap-2 mt-4 justify-end">
                <Button variant="ghost" size="sm" onClick={() => setRenamingKb(null)}>取消</Button>
                <Button variant="gradient" size="sm" onClick={doRename} disabled={!renameInput.trim() || renameInput === renamingKb.name}>保存</Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 删除确认弹窗(输入名二次确认, 企业级防误删) */}
      <AnimatePresence>
        {deletingKb && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
            onClick={() => setDeletingKb(null)}>
            <motion.div initial={{ scale: 0.95 }} animate={{ scale: 1 }} exit={{ scale: 0.95 }}
              className="bg-card rounded-xl shadow-xl w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
              <div className="font-semibold mb-2 flex items-center gap-2 text-destructive"><AlertTriangle className="h-4 w-4" /> 删除知识库</div>
              <p className="text-sm text-muted-foreground mb-3">
                将删除「{deletingKb.name}」及其 {deletingKb.doc_count} 个文档的全部数据（向量/原文/元数据），<b className="text-foreground">不可恢复</b>。
              </p>
              <p className="text-xs text-muted-foreground mb-1">请输入知识库名称确认：</p>
              <Input value={deleteInput} onChange={(e) => setDeleteInput(e.target.value)} placeholder={deletingKb.name} autoFocus />
              <div className="flex gap-2 mt-4 justify-end">
                <Button variant="ghost" size="sm" onClick={() => setDeletingKb(null)}>取消</Button>
                <Button variant="destructive" size="sm" onClick={doDelete} disabled={deleteInput !== deletingKb.name}>
                  <Trash2 className="h-3.5 w-3.5" /> 确认删除
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 文档删除确认弹窗 */}
      <AnimatePresence>
        {deletingDoc && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
            onClick={() => setDeletingDoc(null)}>
            <motion.div initial={{ scale: 0.95 }} animate={{ scale: 1 }} exit={{ scale: 0.95 }}
              className="bg-card rounded-xl shadow-xl w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
              <div className="font-semibold mb-2 flex items-center gap-2 text-destructive"><AlertTriangle className="h-4 w-4" /> 删除文档</div>
              <p className="text-sm text-muted-foreground">将删除文档「{deletingDoc.title}」及其全部向量与原文，<b className="text-foreground">不可恢复</b>。</p>
              <p className="text-xs text-muted-foreground mt-1 mb-4">删除后该文档不再被检索到。</p>
              <div className="flex gap-2 justify-end">
                <Button variant="ghost" size="sm" onClick={() => setDeletingDoc(null)}>取消</Button>
                <Button variant="destructive" size="sm" onClick={doDeleteDoc}>
                  <Trash2 className="h-3.5 w-3.5" /> 确认删除
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
