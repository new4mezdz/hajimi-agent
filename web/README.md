# Agent One Web

AI Agent 产品工作台，使用 Next.js、React、Tailwind CSS 和 Vercel AI SDK。

## 本地启动

先启动仓库根目录下的 FastAPI 服务，再运行：

```powershell
Copy-Item .env.local.example .env.local
pnpm dev
```

访问 <http://127.0.0.1:3000>。环境变量说明见 `.env.local.example`。

## 检查

```powershell
pnpm lint
pnpm build
```
