# Agent Product Starter

一个面向商业产品二次开发的 AI Agent 全栈骨架。后端采用
[Pydantic AI](https://github.com/pydantic/pydantic-ai) 和 FastAPI，前端采用
Next.js、React 与 Vercel AI SDK。

当前版本提供：

- 可替换模型供应商的 Agent 服务层
- 工具调用示例（时区时间查询）
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
- 用户授权的本地代码工作区，以及只读文件浏览、源码搜索和按行读取工具

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

## 启动桌面客户端原型

安装 Rust 工具链后，在 `web` 目录运行：

```powershell
pnpm desktop:dev
```

开发版 Tauri 客户端会自动启动仓库 `.venv` 中的 Python Agent 服务。点击“打开仓库”后，
原生目录选择器会将用户明确授权的代码目录注册为只读工作区。当前版本允许 Agent 列出文件、
搜索源码和按行读取 UTF-8 文本；文件修改、Shell 和 Git 写操作尚未开放。

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
