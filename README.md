# Agent Product Starter

一个面向商业产品二次开发的 AI Agent 全栈骨架。后端采用
[Pydantic AI](https://github.com/pydantic/pydantic-ai) 和 FastAPI，前端采用
Next.js、React 与 Vercel AI SDK。

当前版本提供：

- 可替换模型供应商的 Agent 服务层
- 工具调用示例（时区时间查询与安全算术计算器）
- DeepSeek V4 原生联网搜索（可关闭并限制单次搜索次数）
- FastAPI 健康检查和版本化聊天 API
- SQLite 开发存储、PostgreSQL 生产存储
- Pydantic AI 原生消息历史持久化
- 显式租户边界和可选服务 API Key
- 会话乐观并发控制
- 流式聊天、Markdown、推理过程和工具调用展示
- 响应式产品级 Web 工作台
- 自动化测试和 Docker 部署骨架
- Tauri 2 Windows 桌面客户端原型
- 用户授权的本地代码工作区、文件浏览、源码搜索、按行读取和精确原文读取工具
- 必须逐次人工批准的独占创建、精确文本补丁与兼容整文件写入
- 写入内容预览、敏感路径拦截、大小限制与 SHA-256 并发保护
- 桌面设置中心、模型切换与 Windows DPAPI 加密的 API Key 快速配置
- Git 变更审查、逐文件统一 Diff、状态统计和检查结果汇总
- 一次性确认保护的 Git 提交与非强制推送

## 本地启动

本机已有 Python 3.11，可直接创建独立环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

在 `.env` 中填写所选模型供应商的 API Key，然后启动：

```powershell
uvicorn agent_product.main:app --reload
```

使用 `deepseek:deepseek-v4-flash` 或 `deepseek:deepseek-v4-pro` 并配置
`DEEPSEEK_API_KEY` 时，Agent 会通过 DeepSeek 的 Anthropic 兼容接口启用原生联网搜索。
可以通过 `WEB_SEARCH_ENABLED` 关闭搜索，通过 `WEB_SEARCH_MAX_USES` 限制单次请求的搜索次数。
不需要单独申请搜索服务密钥；仍使用同一个 DeepSeek API Key，但联网搜索请求和相关模型
Token 会计入 DeepSeek 用量。用户明确要求“搜索、联网、最新、今天、实时、价格、天气”等
信息时，服务会强制先搜索一次，再让主 Agent 根据带来源的结果作答。

接口文档：<http://127.0.0.1:8000/docs>

## 启动前端

另开一个 PowerShell 窗口：

```powershell
cd web
Copy-Item .env.local.example .env.local
pnpm install
pnpm dev
```

打开 <http://127.0.0.1:3000>。开发时前端默认连接
`http://127.0.0.1:8000`，也可以在 `web/.env.local` 中修改。

## 启动桌面客户端

安装 Rust 工具链后，在 `web` 目录运行：

```powershell
pnpm desktop:dev
```

开发版 Tauri 客户端会自动启动仓库 `.venv` 中的 Python Agent 引擎。桌面界面不访问
`localhost`；Tauri 与 Python 通过标准输入输出上的 JSONL IPC 交换普通响应、流式消息和取消
事件。点击“打开仓库”后，
原生目录选择器会将用户明确授权的代码目录注册为受控工作区。当前版本允许 Agent 列出文件、
搜索源码，以及按行或保留原始换行读取 UTF-8 文本。Agent 可以提议创建新文件、对唯一匹配的
原文片段应用补丁，或兼容性地整体写入文件；客户端会先显示目标路径和变更预览，只有用户明确
批准后后端才会写入。新建操作绝不覆盖已有目录项，补丁保留未命中的其余内容，完整读取时还可
使用 SHA-256 防止并发修改；Shell 和 Git 写操作尚未开放。

侧栏底部的“设置”（快捷键 `Ctrl+,`）提供模型与 API、Agent 指令、权限和版本信息。桌面版
支持 OpenAI、DeepSeek 和 Anthropic：填写 API Key 后由 Tauri 使用当前 Windows 用户的 DPAPI
加密，配置文件不保存明文，也不会把密钥传入对话或写入项目目录。再次打开设置时只返回固定长度
的短前缀掩码（例如 `sk-1234••••••••`），不返回完整密钥、尾部或真实长度。保存设置会重启本地 Agent，
并自动重新注册已打开的工作区。关闭“允许申请文件写入”后，后端不会向模型注册
`create_file`、`apply_patch` 或 `write_file`；读取仍受已授权工作区和路径规范化规则限制。
浏览器开发模式继续通过 `.env` 配置密钥。

构建无需 Python 环境的 Windows 独立安装包：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
cd web
pnpm desktop:build
```

构建会先将 Python Agent 封装为经过健康检查的 PyInstaller sidecar，再由 Tauri 生成 NSIS
安装包。输出位于 `web/src-tauri/target/release/bundle/nsis/`。安装后用户只需打开客户端；
客户端负责启动、重启和关闭内部 Agent 引擎，不需要终端、Python、浏览器或本地服务端口。

主界面顶部的“变更”会读取当前工作区的 Git 状态，集中展示修改文件、暂存状态、逐文件 Diff、
增删行、分支 ahead/behind 和检查结果。`git diff --check` 会作为只读完整性检查自动执行；项目
测试目前显示为“未运行”，待受控终端接入后再记录真实结果，客户端不会在未确认的情况下执行
仓库代码。

创建提交和推送都使用两阶段确认：服务端先绑定当前仓库指纹生成两分钟有效的一次性确认令牌，
客户端展示提交文件、消息或远端分支，用户再次确认后才执行。确认后仓库内容或 HEAD 发生变化会
自动拒绝；敏感/忽略路径、合并冲突、Git clean filter、仓库本地凭据助手和危险 remote helper
也会阻止操作。Agent 创建提交时禁用 Git hooks，推送永远不使用 `--force`。

## Agent Profile

项目现在把通用 Runtime 与专用 Agent 组合分开。侧栏可以选择“通用助手”“知识助手”或“代码
工作区助手”；切换 Profile 会创建新会话。每个会话固定记录 Profile id、版本和 manifest hash，
不能在已有历史上静默更换工具、知识范围或权限。

Profile 通过 Capability Pack 组合 `common`、`web`、`knowledge`、`workspace-read` 和
`workspace-write`。`GET /v1/agent-profiles` 返回可用组合；普通聊天可传 `profile_id`，流式聊天
使用 `X-Agent-Profile`。完整的专用化方式和知识范围设计见
[`docs/agent-platform.md`](docs/agent-platform.md)。

每个工具有统一风险、审批、并发和超时策略；Runtime 启动时会核对实际工具 metadata 和 JSON
Schema。执行过程追加到 `conversation_events`，可通过
`GET /v1/conversations/{conversation_id}/events` 增量检查 Profile、Prompt、工具目录、审批和
完成/失败状态。

## 本地知识库

第一阶段使用 `knowledge/` 下版本化的 Markdown 或纯文本作为事实知识源。Agent 先通过
`search_knowledge` 获取紧凑命中，再按 `chunk_id` 调用 `read_knowledge_context` 展开同章节上下文，
必要时按文档 ID 读取原文，并在回答中使用文件和行号引用。当前检索为本地关键词与短语排序，
不需要 Embedding 服务或向量数据库。

文档格式和生命周期说明见 [`knowledge/README.md`](knowledge/README.md)，检索接口和安全边界见
[`docs/knowledge-base.md`](docs/knowledge-base.md)。可以通过 `KNOWLEDGE_DIR` 更改知识目录，或用
`KNOWLEDGE_ENABLED=false` 禁用 Agent 的知识工具。

启动桌面客户端后点击聊天侧栏的“知识管理”，即可新建、导入、编辑、发布和归档文档，并用自然
语言问题测试实际检索命中。知识命令通过客户端内部 JSONL IPC 执行，不需要启动本地 HTTP 服务。
保存使用文档 revision 做并发保护；如果文档在编辑期间被其他操作修改，界面会要求重新加载而
不是静默覆盖。客户端本地知识库的 V2 结构见
[`docs/knowledge-system-v2.md`](docs/knowledge-system-v2.md)。

开发和生产环境默认使用持久化增量 SQLite FTS5 索引；`app_env=test` 自动使用内存索引。文件未
变化时会复用文档与 Chunk 快照，只对新增、修改和删除内容更新索引。运行
`python tools/evaluate_knowledge.py evals/knowledge.sample.jsonl` 可以计算固定检索集的 Hit Rate 和 MRR。

流程、规范和 SOP 使用独立的 `skills/` 目录。模型平时只看到已发布 Skill 的名称与描述，匹配
任务后才调用 `load_skill` 读取完整正文；Profile 范围、发布状态和调用策略都会在加载端再次校验。
格式与安全边界见 [`docs/skills.md`](docs/skills.md)。

## 客服 Agent 示例

选择侧栏“客服 Agent”后，可以直接问“我昨天买的耳机还有多久到”或“我前几天买的键盘坏了
能换吗”。启动时会向主 SQLAlchemy 数据库幂等写入脱敏顾客、公开订单号、商品、库存、订单项
快照、物流、支付和库存流水。Agent 从认证会话取得 `customer_id`，用 `find_my_orders` 根据时间、
商品和状态定位顾客自己的订单，再联查业务事实与 `support` 知识，确定性计算退款/换货/人工复核。
它不能执行支付退款或库存扣减；需要动作时只能提议 `create_support_case`，用户批准后才在数据库事务中
创建租户隔离售后工单。数据清单见
[`examples/support/demo-data.json`](examples/support/demo-data.json)。

客服 Profile 还会按需加载 `order-delivery-status`、`after-sales-resolution`、
`refund-exception-review` 和 `delivery-exception-triage` 四个流程 Skill。前端会显示 Skill 加载卡、
版本/revision、流程正文、后续工具结果与审批；这是可审计执行轨迹，不是模型隐藏思维链。

只读演示接口包括：

```text
GET /v1/support/orders?days=30&product_hint=耳机
GET /v1/support/orders/{order_number}
GET /v1/support/orders/{order_number}/items/{line_number}/after-sales-options
GET /v1/support/inventory
GET /v1/support/cases
```

## 调用聊天接口

第一次请求不传 `conversation_id`，服务会创建会话：

```powershell
$body = @{ message = "现在上海几点？" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/chat `
  -ContentType "application/json" `
  -Headers @{ "X-Tenant-ID" = "demo" } `
  -Body $body
```

后续请求把响应中的 `conversation_id` 原样传回，即可继续上下文。

如果配置了 `SERVICE_API_KEY`，调用方还必须发送 `X-API-Key` 请求头。

## 测试与代码检查

```powershell
pytest
ruff check .
```

测试使用 Pydantic AI 的 `TestModel`，不会请求真实模型，也不会产生模型费用。

## Docker 部署

安装 Docker 后：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

提交到生产环境之前，请至少完成：数据库迁移工具、真实身份认证、限流、密钥托管、
HTTPS、集中日志、指标告警、备份恢复和模型输出安全策略。架构边界见
[`docs/architecture.md`](docs/architecture.md)。

## 商用与许可证

本仓库没有把你的业务代码强制发布为开源。核心依赖 Pydantic AI 使用 MIT 许可证，
允许商业使用，但分发产品时仍应保留相关版权与许可声明。模型服务、数据和其他依赖
需要分别遵守各自条款，发布前请完成依赖许可证审计。
