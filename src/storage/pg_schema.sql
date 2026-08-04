-- ponytail: W1最小表结构,只为入库流水线跑通

-- 租户与用户
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50) REFERENCES tenants(id),
    email VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 知识库
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50) REFERENCES tenants(id),
    name VARCHAR(100) NOT NULL,
    embedding_model VARCHAR(50) DEFAULT 'BGE-M3',
    config_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(50) PRIMARY KEY,
    kb_id VARCHAR(50) REFERENCES knowledge_bases(id),
    title VARCHAR(200) NOT NULL,
    active_version_id VARCHAR(50),
    acl_policy JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档版本
CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR(50) PRIMARY KEY,
    document_id VARCHAR(50) REFERENCES documents(id),
    oss_key VARCHAR(500) NOT NULL,
    sha256 VARCHAR(64),
    file_size BIGINT,
    status VARCHAR(20) DEFAULT 'UPLOADED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chunk元数据
CREATE TABLE IF NOT EXISTS chunks (
    id VARCHAR(50) PRIMARY KEY,
    version_id VARCHAR(50) REFERENCES document_versions(id),
    parent_id VARCHAR(50),
    page INTEGER,
    heading_path TEXT[],
    content_hash VARCHAR(64),
    oss_key TEXT,  -- ponytail: 原文片段存OSS,这里只存key
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 入库任务
CREATE TABLE IF NOT EXISTS indexing_jobs (
    id VARCHAR(50) PRIMARY KEY,
    version_id VARCHAR(50) REFERENCES document_versions(id),
    stage VARCHAR(50) DEFAULT 'PARSING',
    status VARCHAR(20) DEFAULT 'PENDING',
    retry_count INTEGER DEFAULT 0,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 会话与消息
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50) REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(50) PRIMARY KEY,
    conversation_id VARCHAR(50) REFERENCES conversations(id),
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    citations JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
