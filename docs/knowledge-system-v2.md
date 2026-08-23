# 客户端本地知识库 V2 设计

## 1. 设计结论

知识库属于桌面客户端，而不是一个需要部署和运维的后端服务。生产版的完整链路是：

```text
知识管理界面
  -> Tauri 本地命令 / JSONL IPC
  -> 随客户端启动的本地 Agent 引擎
  -> 知识库目录 + 本地 SQLite 索引
  -> Agent 检索工具
```

客户端不监听端口，不依赖浏览器、Python 安装或远程数据库。React 负责交互，Tauri 负责
桌面生命周期、原生文件授权和安全存储，本地 Agent 引擎负责内容解析、索引、检索以及与
Agent 工具共用同一份知识数据。

不把知识正文放进 `localStorage`，也不在 React、Rust 和 Python 中维护三份互相同步的数据。
原始来源是事实数据，索引是随时可以重建的派生数据。

## 2. 产品模型

V2 使用四层模型：

1. **知识库（Library）**：类似一个项目，是用户看到和管理的最高层容器。
2. **来源（Source）**：用户添加的一份笔记、文件、文件夹或后续支持的网页。
3. **文档（Document）**：来源经过解析和规范化后得到的可读内容。一份来源可以产生多份文档。
4. **分块（Chunk）**：用于检索的最小单元，保存标题层级、页码或行号等引用信息。

单 Agent 不等于只能有一个知识库。首版默认创建“个人知识库”，Agent 检索所有已启用的知识库；
数据模型从一开始保留多个知识库，界面可以先隐藏复杂的作用域配置。

### 生命周期

- 知识库：`active`、`paused`、`archived`
- 来源：`pending`、`indexing`、`ready`、`stale`、`error`、`excluded`
- 手写笔记：`draft`、`published`、`archived`

只有启用知识库中的 `ready` 来源和 `published` 笔记可以被 Agent 检索。这样保留明确的人工信任边界，
导入一份文件不会在用户确认前悄悄改变 Agent 的长期知识。

## 3. 本地数据布局

生产环境以 Tauri 解析出的应用数据目录为根目录：

```text
<app-data>/
  data/
    agent.db                 # 对话、知识库目录和来源元数据
    knowledge-index.db       # 分块、词法索引和索引任务状态
  knowledge/
    <library-id>/
      notes/                 # 客户端内创建的 Markdown 笔记
      imports/               # 用户选择“复制到知识库”的原始文件
      extracted/             # 可重建的规范化文本
```

开发模式可以继续把仓库中的 `knowledge/` 视为兼容知识库。迁移时将它注册为默认知识库，已有
Markdown 文档 ID、revision 和行号引用保持不变，不需要一次性重写现有数据。

### 来源策略

- **创建笔记**：正文由客户端管理，原子写入 Markdown 文件。
- **导入文件**：默认复制到知识库，后续原文件删除不会导致知识丢失。
- **链接文件夹**：后续能力；保留外部路径并监听变化，明确显示“外部来源”。
- **索引**：只保存派生数据，可以通过“重建索引”完整恢复。

## 4. 导入与索引流程

```text
原生文件选择器
  -> Tauri 将用户明确选择的文件复制进受控导入目录
  -> 本地引擎计算 SHA-256 并创建来源记录
  -> 解析为规范化文档
  -> 按标题、段落和页码切分
  -> 建立词法索引
  -> 标记 ready
```

不能新增一个接受任意绝对路径的知识导入接口。原生选择与复制应由同一个 Tauri 命令完成，避免
网页层伪造路径后读取用户未授权的文件。首期继续支持 Markdown 和 UTF-8 纯文本；PDF、DOCX、
HTML 和目录同步进入后续阶段。

## 5. 检索契约

Agent 只需要稳定的两个工具，不感知索引实现：

- `search_knowledge(query, library_ids?, tags?, limit?)`
- `read_knowledge_context(chunk_id)`
- `read_knowledge_document(document_id, location?)`

每个命中必须返回：

- `library_id`、`source_id`、`document_id` 和稳定 URI；
- 文档标题、章节和片段；
- Markdown 行号，或 PDF 页码等来源定位；
- 内容 hash / 更新时间，用于判断引用是否过期；
- 可直接展示的引用文本。

首期沿用当前本地词法与中文二/三元切分，不引入外部 Embedding 服务。开发和生产默认使用
`sqlite_fts5`：按 `content_hash` 增量添加、更新和删除 Chunk，使用 WAL，并针对中文写入与
内存索引相同的单字/二元/三元检索 token；重启后无需丢失索引。未来可以在同一契约上增加
本地或云端向量召回，向量库不是事实数据源。

### Chunk Policy V1

当前实现采用结构优先策略：目标 480 tokens、软上限 600、硬上限 800、普通小块合并阈值 80。
标题与自然段边界不重叠；只有单个语义元素超过硬上限时才固定切分，并保留 80 tokens 重叠。
Markdown 表格和围栏代码块保持独立。检索对子块评分并默认只返回紧凑命中；模型确认候选后通过
`read_knowledge_context` 读取同章节父级上下文，父级目标 1,200 tokens、硬上限 1,500。策略通过
`GET /v1/knowledge/chunk-policy` 暴露，并在每个命中中返回版本号，以便参数变化后识别和重建旧索引。

## 6. 客户端内部命令边界

现有 `/v1/knowledge/...` 路径可以暂时保留，作为 JSONL IPC 中的版本化本地命令名，而不是公网
HTTP API。浏览器模式的 HTTP transport 仅用于开发和自动化测试。

V2 建议逐步形成以下命令：

```text
GET  /v1/knowledge/libraries
POST /v1/knowledge/libraries
PUT  /v1/knowledge/libraries/{library_id}

GET  /v1/knowledge/libraries/{library_id}/sources
POST /v1/knowledge/libraries/{library_id}/notes
POST /v1/knowledge/libraries/{library_id}/reindex

POST /v1/knowledge/search
GET  /v1/knowledge/documents/{document_id}
```

当前已实现只读目录入口：`GET /v1/knowledge/libraries` 暴露文件系统 Provider 的稳定 `default`
Library，`GET /v1/knowledge/libraries/default/sources` 把每篇受管文档投影为稳定 Source。搜索结果和
文档读取都返回 `library_id`、`source_id`；后续增加持久化多 Library 时保持这些 ID 与路由契约。

索引实现通过 `KnowledgeIndex` 协议替换，当前词法后端和未来的向量/混合后端共用 `sync`、
`search`、`status` 契约。Embedding 通过独立的 `EmbeddingModel` 协议接入；模型只负责批量文档
向量与查询向量，不拥有原文、生命周期或权限。`GET /v1/knowledge/index-status` 可检查当前后端、
检索模式、Chunk 数量及未来启用的 Embedding 模型和维度。

### Agent Profile 与知识范围

`KnowledgeIndex` 决定“如何召回”，不决定“哪个 Agent 可以看到什么”。Agent 通过稳定的
`KnowledgeProvider` 读取知识，再由会话绑定 Profile 的 `KnowledgeScope` 收紧范围：

- `required_tags` 同时限制搜索和直接按文档 ID 读取；
- `library_ids` 已可限制当前文件系统 Provider 的 `default` Library；V2.1 可增加更多稳定 id；
- 没有 Knowledge Scope 的 Profile 不注册知识工具，也不会收到知识 Provider；
- Profile 只能读取已发布知识，知识创建、导入、发布和归档仍属于人类管理界面。

因此索引替换不会改变权限，模型也不能通过猜测 `document_id` 绕过搜索范围。完整装配关系见
[`agent-platform.md`](agent-platform.md)。

文件选择不是上述知识命令的一部分，由 Tauri 原生导入命令负责。写操作继续使用 revision/hash
进行乐观并发控制，并采用同目录临时文件加原子替换。

## 7. 界面信息架构

知识管理界面采用“创建项目并添加文件”的心智模型：

```text
知识库列表 | 来源列表       | 内容/状态/错误详情      | 检索测试
个人知识库 | 产品说明.md    | 预览、元数据、是否可用  | 问题与实际命中
项目知识库 | API 文档文件夹 | 同步状态、重建索引      | 引用与分数
```

首屏只保留三个主要动作：

1. 新建知识库；
2. 添加文件或创建笔记；
3. 启用给 Agent。

“草稿/发布/归档”只出现在可编辑笔记上；导入文件显示“处理中/可用/已过期/失败”，避免把内容发布
状态和索引任务状态混在一起。检索测试面板保留，因为它是判断“Agent 到底能不能找到这份知识”
最直接的质量反馈。

## 8. 安全边界

- Agent 的知识工具只读，不能自行创建、修改或发布长期知识。
- 所有导入必须来源于一次明确的原生文件选择。
- 默认复制导入，外部路径同步必须单独授权并可随时解除。
- 拒绝符号链接逃逸、超限文件、非预期格式和包含凭据风险的文件名。
- 文档内的提示词属于参考数据，不可覆盖系统指令或获得工具权限。
- 删除知识库先进入可恢复归档；真正清除原始文件必须二次确认。

## 9. 交付顺序

### V2.0：适配当前客户端

- 将现有知识接口明确为本地 IPC 命令；
- 生产数据固定落在应用数据目录；
- 保留单一默认知识库和 Markdown/TXT 管理能力；
- 移除界面与文档中的“启动后端服务”概念。

### V2.1：知识库与来源

- 增加 Library 和 Source 数据模型（已完成 `default` Library/Source 只读投影与 Profile 范围）；
- 增加“新建知识库”和原生安全导入；
- 展示索引进度、失败原因、hash 和更新时间；
- 当前 `knowledge/` 自动迁移成默认知识库。

### V2.2：持久化索引

- SQLite FTS 分块索引、文档/Chunk 缓存和增量重建（已完成）；
- PDF、DOCX、HTML 本地解析；
- 文件夹同步、变更检测和失效引用提示；
- 建立固定检索问题集，衡量召回质量而不是只看界面结果。

## 10. 当前不做

- 不部署知识库服务器或向量数据库；
- 不让模型自动写入长期记忆；
- 不把聊天记录自动当作可信知识；
- 不在首版加入团队共享、权限角色和多租户；
- 不为了“语义检索”立即引入外部 Embedding 成本和隐私风险。
