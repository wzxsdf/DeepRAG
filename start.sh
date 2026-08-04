#!/usr/bin/env bash
# DeepRAG 一键启动: 后端(:8000) + 前端(:5173)
# 用法: 在 Git Bash 里  bash start.sh   (或 ./start.sh)
# 单终端合并两边日志, Ctrl+C 停止全部

cd /e/DeepRAG || { echo "找不到 /e/DeepRAG"; exit 1; }

# Ctrl+C / 终止时, taskkill 杀进程树(防 uvicorn/vite 子进程残留占用端口), fallback kill
cleanup() {
  echo
  echo "[start] 停止全部服务..."
  for p in $(jobs -p); do
    taskkill //F //T //PID "$p" 2>/dev/null || kill "$p" 2>/dev/null
  done
  exit 0
}
trap cleanup INT TERM

echo "[start] 启动后端(:8000) + 前端(:5173), Ctrl+C 停止全部"
echo

# 后台跑, 实时加前缀(fflush 防缓冲); 两个管道的 stdout 合并到当前终端
python -m apps.api.main 2>&1 | awk '{print "[backend]  " $0; fflush()}' &
(cd web && npm run dev) 2>&1 | awk '{print "[frontend] " $0; fflush()}' &

wait
