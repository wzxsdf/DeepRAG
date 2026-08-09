# DeepRAG API 参考文档

> 完整的 RESTful API 文档，基于 FastAPI 自动生成 OpenAPI 3.1 规范

## 📖 文档访问

启动 API 服务后，可通过以下地址访问交互式文档：

| 地址 | 说明 |
|------|------|
| `http://localhost:8000/docs` | Swagger UI 交互式文档 |
| `http://localhost:8000/redoc` | ReDoc 文档(更美观) |
| `http://localhost:8000/openapi.json` | OpenAPI 3.1 JSON 规范 |

## 🔐 认证机制

### JWT Token 认证

除注册和登录外，所有接口需要 Bearer Token 认证：

```http
Authorization: Bearer <your_jwt_token>
```

### 获取 Token

```bash
# 登录获取 token
curl -X POST http://localhost:8000/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "your_password"}'
```

响应：
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "user-uuid",
    "email": "user@example.com",
    "name": "用户名"
  }
}
```

---

## 📋 API 端点总览

### 🔑 认证接口 (auth)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/register` | 用户注册 |
| POST | `/api/v1/login` | 用户登录 |
| GET | `/api/v1/me` | 获取当前用户信息 |

### 💬 问答接口 (query)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/query` | 智能问答(单次) |
| POST | `/api/v1/query/stream` | 流式问答(SSE) |

### 📝 对话接口 (conversations)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/conversations` | 获取对话列表 |
| GET | `/api/v1/conversations/{conv_id}/messages` | 获取对话消息 |

### 📚 知识库接口 (knowledge-base)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/kbs` | 创建知识库 |
| GET | `/api/v1/kbs` | 获取知识库列表 |
| PATCH | `/api/v1/kbs/{kb_id}` | 更新知识库 |
| DELETE | `/api/v1/kbs/{kb_id}` | 删除知识库 |

### 📄 文档接口 (documents)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/uploads/presign` | 获取上传预签名URL |
| POST | `/api/v1/documents/confirm` | 确认文档上传 |
| GET | `/api/v1/documents` | 获取文档列表 |
| GET | `/api/v1/documents/{document_id}/preview` | 预览文档 |
| GET | `/api/v1/documents/{document_id}` | 获取文档详情 |
| DELETE | `/api/v1/documents/{document_id}` | 删除文档 |

### 📊 监控接口 (monitor)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/metrics` | 获取系统指标 |

---

## 🚀 核心接口详解

### 1. 智能问答 - 单次查询

```http
POST /api/v1/query
Authorization: Bearer <token>
Content-Type: application/json
```

**请求体：**
```json
{
  "question": "DeepRAG的检索层用了什么技术?",
  "tenant_id": "default",
  "kb_ids": ["kb-uuid-1"],
  "conversation_id": null,
  "mode": "kb"
}
```

**参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | ✅ | 查询问题 |
| `tenant_id` | string | ❌ | 租户ID(默认default) |
| `kb_ids` | array | ❌ | 限定知识库(空=全部) |
| `conversation_id` | string | ❌ | 对话ID(多轮复用) |
| `mode` | string | ❌ | 查询模式: quick/kb/agent |

**响应：**
```json
{
  "answer": "DeepRAG检索层采用 BGE-M3 密集向量 [1] 与 Qdrant 原生 BM25 [2]...",
  "citations": [
    {
      "chunk_id": "chunk-uuid",
      "document_id": "doc-uuid",
      "page": 5
    }
  ],
  "retry_count": 0,
  "grounded": true,
  "conversation_id": "conv-uuid",
  "source": "kb_grounded"
}
```

**curl 示例：**
```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "DeepRAG的检索层用了什么技术?",
    "mode": "kb"
  }'
```

### 2. 流式问答 - SSE 实时推送

```http
POST /api/v1/query/stream
Authorization: Bearer <token>
Content-Type: application/json
```

返回 **Server-Sent Events** 流，包含三种事件类型：

**事件类型：**

| 事件 | 说明 | 示例 |
|------|------|------|
| `step` | Agent节点进度 | 节点中间结果(检索/评估/生成) |
| `thought` | Agent推理过程 | 工具调用、多源规划 |
| `token` | Token级输出 | LLM逐字生成 |
| `done` | 完成事件 | 最终答案+引用 |

**JavaScript 接收示例：**
```javascript
const response = await fetch('/api/v1/query/stream', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    question: 'DeepRAG如何工作?',
    mode: 'agent'
  })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop();

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      const eventType = line.slice(7);
      // 处理不同事件
    }
    if (line.startsWith('data: ')) {
      const data = JSON.parse(line.slice(6));
      console.log(data);
    }
  }
}
```

### 3. 知识库管理

#### 创建知识库
```bash
curl -X POST http://localhost:8000/api/v1/kbs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "技术文档库"}'
```

#### 上传文档(三步流程)

**Step 1: 获取预签名URL**
```bash
curl -X POST http://localhost:8000/api/v1/uploads/presign \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename": "document.pdf", "kb_id": "kb-uuid"}'
```

**Step 2: 直传OSS(使用预签名URL)**
```bash
curl -X PUT "$PRESIGN_URL" \
  --data-binary @document.pdf
```

**Step 3: 确认上传**
```bash
curl -X POST http://localhost:8000/api/v1/documents/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kb_id": "kb-uuid", "filename": "document.pdf"}'
```

### 4. 对话历史

#### 获取对话列表
```bash
curl http://localhost:8000/api/v1/conversations \
  -H "Authorization: Bearer $TOKEN"
```

#### 获取对话消息
```bash
curl http://localhost:8000/api/v1/conversations/{conv_id}/messages \
  -H "Authorization: Bearer $TOKEN"
```

---

## 📊 数据模型

### QueryRequest
```typescript
interface QueryRequest {
  question: string;           // 查询问题
  tenant_id?: string;         // 租户ID(默认default)
  kb_ids?: string[];          // 限定知识库
  conversation_id?: string;   // 对话ID(多轮复用)
  mode?: 'quick' | 'kb' | 'agent';  // 查询模式
}
```

### QueryResponse
```typescript
interface QueryResponse {
  answer: string;             // 答案内容
  citations: Citation[];      // 引用列表
  retry_count: number;        // 自纠次数
  grounded: boolean;          // 是否有据
  conversation_id: string;    // 对话ID
  source: string;             // 答案来源
}

interface Citation {
  chunk_id: string;           // 文档块ID
  document_id: string;        // 文档ID
  page: number;               // 页码
}
```

### KnowledgeBase
```typescript
interface KnowledgeBase {
  id: string;                 // 知识库ID
  name: string;               // 知识库名称
  doc_count: number;          // 文档数量
  created_at: string;         // 创建时间
}
```

---

## ⚡ 性能优化

### 缓存策略

系统内置 Redis 查询缓存，缓存命中时直接返回结果：

- **查询缓存**: 相同问题1小时内直接返回(TTL=3600s)
- **文档缓存**: 文档内容缓存2小时(TTL=7200s)
- **语义缓存**: 语义相似问题共享缓存(阈值0.85)

### 流式响应

使用 SSE 流式输出，减少首字节延迟：
- 节点进度实时推送(`step`事件)
- Token级打字机效果(`token`事件)
- 平均首字节延迟 < 2s

---

## 🛠️ 错误处理

### 错误响应格式
```json
{
  "detail": "错误描述信息",
  "status_code": 400
}
```

### 常见错误码

| 状态码 | 说明 | 解决方案 |
|--------|------|----------|
| 400 | 请求参数错误 | 检查请求体格式 |
| 401 | 未认证 | 检查Authorization头 |
| 403 | 无权限 | 检查租户权限 |
| 404 | 资源不存在 | 检查ID是否正确 |
| 429 | 请求过多 | 降低请求频率 |
| 500 | 服务器错误 | 联系管理员 |

### 速率限制

- 普通接口: 100次/分钟
- 查询接口: 30次/分钟
- 上传接口: 10次/分钟

---

## 🔌 SDK 集成

### Python SDK 示例

```python
import httpx

class DeepRAGClient:
    def __init__(self, base_url: str, token: str):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"}
        )

    async def query(self, question: str, mode: str = "kb"):
        response = await self.client.post(
            "/api/v1/query",
            json={"question": question, "mode": mode}
        )
        return response.json()

    async def stream_query(self, question: str, mode: str = "agent"):
        async with self.client.stream(
            "POST",
            "/api/v1/query/stream",
            json={"question": question, "mode": mode}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield line[6:]
```

### JavaScript/TypeScript SDK 示例

```typescript
class DeepRAGClient {
  constructor(private baseUrl: string, private token: string) {}

  async query(question: string, mode: string = 'kb'): Promise<QueryResponse> {
    const response = await fetch(`${this.baseUrl}/api/v1/query`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ question, mode })
    });
    return response.json();
  }

  async *streamQuery(question: string, mode: string = 'agent') {
    const response = await fetch(`${this.baseUrl}/api/v1/query/stream`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ question, mode })
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      yield decoder.decode(value);
    }
  }
}
```

---

## 📈 监控接口

### 获取系统指标

```bash
curl http://localhost:8000/api/v1/metrics \
  -H "Authorization: Bearer $TOKEN"
```

返回系统运行指标，包括：
- 查询次数和平均延迟
- 缓存命中率
- 文档数量
- 活跃用户数

---

## 🧪 测试

### 健康检查
```bash
curl http://localhost:8000/health
```

### API测试脚本
项目内置测试脚本(`scripts/test_api.py`)：
```bash
python scripts/test_api.py
```

---

## 📝 更新日志

### v1.0.0 (2026-08)
- ✅ 完成 CRAG 状态机
- ✅ 支持三种查询模式
- ✅ SSE 流式输出
- ✅ 多轮对话持久化
- ✅ Redis 查询缓存
- ✅ 记忆模块集成

---

如需了解更多细节，请访问交互式文档：`http://localhost:8000/docs`
