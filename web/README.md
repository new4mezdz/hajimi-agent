# Agent One Web

AI Agent 产品工作台，使用 Next.js、React、Tailwind CSS 和 Vercel AI SDK。

## 本地启动

浏览器开发模式需要先启动仓库根目录下的 FastAPI 服务，再运行：

```powershell
Copy-Item .env.local.example .env.local
pnpm dev
```

访问 <http://127.0.0.1:3000>。环境变量说明见 `.env.local.example`。

## 桌面客户端

桌面模式不连接 localhost，也不要求用户安装或手动启动 Python。Tauri 会自动启动打包后的
Agent sidecar，并通过标准输入输出上的 JSONL IPC 传递普通响应、流式消息和取消事件。

开发模式：

```powershell
pnpm desktop:dev
```

构建独立 Windows 安装包：

```powershell
cd ..
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
cd web
pnpm desktop:build
```

NSIS 安装包输出到 `src-tauri/target/release/bundle/nsis/`。

## 检查

```powershell
pnpm lint
pnpm build
```
