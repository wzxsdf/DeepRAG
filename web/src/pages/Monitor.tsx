import { useQuery } from "@tanstack/react-query"
import { motion } from "framer-motion"
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from "recharts"
import { FileText, Database, MessageSquare, Hash, Activity, Server } from "lucide-react"
import { monitorApi } from "@/api/monitor"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const JOB_COLORS = ["#f59e0b", "#10b981", "#ef4444", "#6366f1", "#6b7280"]

export default function Monitor() {
  const { data } = useQuery({ queryKey: ["metrics"], queryFn: monitorApi.metrics, refetchInterval: 30000 })

  const kpis = [
    { label: "文档", value: data?.documents ?? 0, icon: FileText, color: "from-blue-500 to-cyan-500" },
    { label: "Chunks", value: data?.chunks ?? 0, icon: Database, color: "from-violet-500 to-purple-500" },
    { label: "会话", value: data?.conversations ?? 0, icon: MessageSquare, color: "from-emerald-500 to-teal-500" },
    { label: "消息", value: data?.messages ?? 0, icon: Hash, color: "from-amber-500 to-orange-500" },
  ]

  const jobData = data ? Object.entries(data.jobs).map(([name, value]) => ({ name, value })) : []

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="mb-6 flex items-center justify-between">
          <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}>
            <h1 className="text-2xl font-bold">系统监控</h1>
            <p className="text-muted-foreground text-sm mt-1">实时系统状态, 30s 自动刷新</p>
          </motion.div>
          <Badge variant="success" className="gap-1">
            <Activity className="h-3 w-3 animate-pulse" /> 实时
          </Badge>
        </div>

        {/* KPI 卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {kpis.map((k, i) => (
            <motion.div key={k.label} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
              <Card className="hover:shadow-md transition-shadow">
                <CardContent className="p-5">
                  <div className={cn("mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br shadow-sm", k.color)}>
                    <k.icon className="h-5 w-5 text-white" />
                  </div>
                  <div className="text-2xl font-bold">{k.value}</div>
                  <div className="text-sm text-muted-foreground">{k.label}</div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        <div className="grid md:grid-cols-2 gap-4">
          {/* 入库状态饼图 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">入库任务状态分布</CardTitle>
            </CardHeader>
            <CardContent>
              {jobData.length > 0 ? (
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie data={jobData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
                      {jobData.map((_, i) => (
                        <Cell key={i} fill={JOB_COLORS[i % JOB_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-[240px] flex items-center justify-center text-muted-foreground text-sm">暂无任务</div>
              )}
            </CardContent>
          </Card>

          {/* 依赖健康 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Server className="h-4 w-4" /> 依赖服务健康
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 pt-2">
              {data?.dependencies &&
                Object.entries(data.dependencies).map(([name, status]) => (
                  <motion.div
                    key={name}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="flex items-center justify-between py-2 border-b last:border-0"
                  >
                    <span className="font-medium uppercase text-sm tracking-wide">{name}</span>
                    <Badge variant={status === "ok" ? "success" : "destructive"} className="gap-1.5">
                      <span className={cn("h-2 w-2 rounded-full bg-white", status !== "ok" && "animate-pulse")} />
                      {status}
                    </Badge>
                  </motion.div>
                ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
