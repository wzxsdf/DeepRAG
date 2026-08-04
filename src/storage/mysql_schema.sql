-- ponytail: W1最小表结构,MySQL语法

-- 租户与用户
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50),
    email VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 知识库
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50),
    name VARCHAR(100) NOT NULL,
    embedding_model VARCHAR(50) DEFAULT 'BGE-M3',
    config_json JSON,
    status VARCHAR(20) DEFAULT 'ACTIVE',  -- ponytail: 生命周期 ACTIVE/DELETING/DELETED, 并发保护
    deleted_at TIMESTAMP NULL,             -- 软删时间(NULL=未删), 列表/检索过滤
    deleted_by VARCHAR(50),                -- 软删操作者user_id(审计)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 文档
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(50) PRIMARY KEY,
    kb_id VARCHAR(50),
    title VARCHAR(200) NOT NULL,
    active_version_id VARCHAR(50),
    acl_policy JSON,
    deleted_at TIMESTAMP NULL,   -- ponytail: 软删时间(NULL=未删)
    deleted_by VARCHAR(50),      -- 软删操作者(审计)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 文档版本
CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR(50) PRIMARY KEY,
    document_id VARCHAR(50),
    oss_key VARCHAR(500) NOT NULL,
    sha256 VARCHAR(64),
    file_size BIGINT,
    status VARCHAR(20) DEFAULT 'UPLOADED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Chunk元数据
CREATE TABLE IF NOT EXISTS chunks (
    id VARCHAR(50) PRIMARY KEY,
    version_id VARCHAR(50),
    parent_id VARCHAR(50),
    page INTEGER,
    heading_path TEXT,  -- ponytail: MySQL没有数组,用JSON或TEXT
    content_hash VARCHAR(64),
    oss_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (version_id) REFERENCES document_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 入库任务
CREATE TABLE IF NOT EXISTS indexing_jobs (
    id VARCHAR(50) PRIMARY KEY,
    version_id VARCHAR(50),
    stage VARCHAR(50) DEFAULT 'PARSING',
    status VARCHAR(20) DEFAULT 'PENDING',
    retry_count INTEGER DEFAULT 0,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (version_id) REFERENCES document_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 会话与消息
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(50) PRIMARY KEY,
    conversation_id VARCHAR(50),
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    citations JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
