# ponytail: 领域模型(dataclass) — 替代API层裸dict操作, 给Repository类型化契约
# 实体对应库表; DTO承载JOIN/聚合结果(非纯实体). 与storage/protocol.py的检索模型(RetrievedDocument)区分

from dataclasses import dataclass


# ============ 实体(对应单表) ============

@dataclass
class User:
    id: str
    tenant_id: str
    email: str
    name: str = ""
    password_hash: str = ""
    created_at: str | None = None


@dataclass
class KnowledgeBase:
    id: str
    tenant_id: str
    name: str
    status: str = "ACTIVE"
    deleted_at: str | None = None
    deleted_by: str | None = None
    created_at: str | None = None


@dataclass
class Document:
    id: str
    kb_id: str
    title: str
    deleted_at: str | None = None
    deleted_by: str | None = None
    created_at: str | None = None


@dataclass
class DocumentVersion:
    id: str
    document_id: str
    oss_key: str
    sha256: str | None = None
    file_size: int | None = None
    status: str = "UPLOADED"
    created_at: str | None = None


@dataclass
class IndexingJob:
    id: str
    version_id: str
    stage: str = "PARSING"
    status: str = "PENDING"
    retry_count: int = 0
    error_msg: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class ChunkMeta:
    # ponytail: chunks表元数据(与检索结果RetrievedDocument区分, 后者含content/score)
    id: str
    version_id: str
    parent_id: str | None = None
    page: int | None = None
    heading_path: str | None = None
    content_hash: str | None = None
    oss_key: str | None = None
    created_at: str | None = None


@dataclass
class ConversationMessage:
    id: int | None = None
    conversation_id: str = ""
    user_id: str = "default"
    role: str = ""
    content: str = ""
    created_at: str | None = None


# ============ DTO(JOIN/聚合结果, 非纯实体) ============

@dataclass
class KBWithCount:
    # GET /kbs 列表: KB + 文档数聚合
    id: str
    name: str
    doc_count: int = 0
    created_at: str | None = None


@dataclass
class DocumentListItem:
    # GET /documents 列表: 3表JOIN(documents LEFT JOIN versions LEFT JOIN jobs)
    id: str
    title: str
    kb_id: str
    status: str = "UNKNOWN"  # version_status or job_status or UNKNOWN
    stage: str | None = None
    created_at: str | None = None


@dataclass
class DocumentDetail:
    # GET /documents/{id} 详情: 3表JOIN
    document_id: str
    title: str
    version_id: str | None = None
    status: str = "UNKNOWN"
    stage: str | None = None
    error_msg: str | None = None


@dataclass
class ConversationSummary:
    # GET /conversations 聚合: 每会话首条消息(作标题)/消息数/最后时间
    id: str
    title: str
    messages: int
    updated_at: str | None = None
