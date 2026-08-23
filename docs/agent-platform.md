# Agent 专用化平台

## 目标

本项目的目标不是把所有能力永久装进一个 Agent，而是提供一个稳定 Runtime，通过版本化
Agent Profile 和模块化 Capability Pack 快速派生专用 Agent：

```text
Runtime
  + Agent Profile
  + Capability Packs
  + Knowledge Scope
  + Permission Policy
  + UI Features
  = Domain Agent
```

新增专用 Agent 的目标验收条件是：增加一个 Profile、必要的领域 Capability Pack 和测试，
不修改会话、审批、模型适配或桌面 IPC Runtime。

## 当前组成

### Runtime

Runtime 负责模型装配、会话、流式响应、工具调用、审批、租户、工作区绑定和持久化。
`AgentRuntime` 在进程启动时构造所有已注册 Profile；每个 Profile 的工具和 Prompt 在会话开始前
固定。

### Agent Profile

Profile 声明：

- 稳定 `id` 和显式 `version`；
- Capability Pack 列表；
- 权限策略；
- Agent 可见的知识范围；
- UI Feature；
- 可选 persona。

Manifest 内容会计算 SHA-256。会话在独立的 `conversation_profiles` 表记录 Profile id、version
和 manifest hash。恢复会话时三者必须一致；若要改变工具或 Prompt，必须升级 Profile version
并新建会话，不能让已有历史静默漂移。

当前内置 Profile：

| Profile | 能力 | 知识 | 本地写入 |
|---|---|---|---|
| `general` | 时间、计算、可选联网 | 无 | 无 |
| `knowledge` | 通用能力、知识检索 | Profile-scoped，只读 | 无 |
| `code` | 知识、代码工作区、受控写入 | Profile-scoped，只读 | 每次审批 |
| `support` | 演示订单、确定性退款判断、客服工单 | `support` 标签 + `default` Library | 工单逐次审批 |

默认 Profile 由 `DEFAULT_AGENT_PROFILE` 设置，当前为 `code`，保持旧客户端行为。
`AGENT_PROFILE_DIR` 可以指向额外的声明式 JSON Profile 目录；它们只能组合已注册 Pack，不能加载
Python 或执行配置代码。仓库中的 [`../agent_profiles/support.example.json`](../agent_profiles/support.example.json)
展示了只需配置即可派生的标签限定客服知识 Agent。

### Capability Pack

一个 Pack 提供有序 Prompt Section 和工具注册函数。当前拆分为：

- `common`
- `web`
- `knowledge`
- `skills`
- `workspace-read`
- `workspace-write`

部署设置是外层能力门：关闭知识、联网或工作区写入后，内置 Profile 不会声明对应 Pack。
每个工具由一个 `ToolPolicy` 同时驱动 Pydantic AI 执行选项和观测目录：

- `risk`: `read`、`network` 或 `write`；
- `approval`: `automatic` 或 `required`；
- `concurrency`: `parallel` 或 `exclusive`；
- `timeout_seconds`；
- 所属能力类别。

写工具必须逐次审批并独占执行；非写工具不能伪装成需要审批的写操作。Runtime 启动时比较
Profile 声明、实际注册工具、metadata、审批、并发、超时和 JSON Schema，任一漂移都会 fail
loud。Profile API 返回实际生效 Pack、工具策略以及 Prompt/Tool Schema/Composition hash。

## API 与客户端

`GET /v1/agent-profiles` 返回可选 Profile、能力、权限、知识范围和 UI Feature。普通聊天可以在
请求体传 `profile_id`；流式聊天使用 `X-Agent-Profile`。未指定时使用默认 Profile。

Profile 只在新会话创建时选择。继续已有会话时可以不重复提交；若提交不同 Profile，服务返回
`409`。Web/Tauri 客户端切换 Profile 时自动创建新会话，并根据 `ui_features` 隐藏知识或工作区
入口。

### Append-only 会话事件

`conversation_events` 追加记录 `conversation.created`、`turn.started`、`request.prepared`、
`message.persisted`、`approval.requested/decided`、`turn.completed/failed/rejected`。请求快照包含
实际静态 system prompt、动态日期、模型、知识范围、工具策略和完整工具 JSON Schema，并记录
各自 hash。`GET /v1/conversations/{id}/events` 支持 `after_id` 增量读取。

现阶段 Pydantic AI 原生 `history_json` 仍是兼容消息 projection；事件流已经承担审计、装配重现
和评测输入，下一阶段再让中断恢复和消息 projection 完全从事件派生。

## 知识库在平台中的位置

知识库分四层，不能和 Agent Profile 或向量索引混为一体：

```text
Source of truth
  Library -> Source -> Document
                |
                v
Derived retrieval
  Chunk Policy -> KnowledgeIndex -> lexical/vector/rerank
                |
                v
Access boundary
  KnowledgeProvider -> KnowledgeScope
                |
                v
Agent capability
  search_knowledge / read_knowledge_context / read_knowledge_document
```

职责如下：

1. `KnowledgeBase` 管理原文、生命周期、revision、Chunk 和引用。
2. `KnowledgeIndex` 只管理可重建的检索数据，可替换为 SQLite FTS、向量或混合索引。
3. `KnowledgeProvider` 是 Agent 使用的稳定读接口，隐藏索引实现。
4. `KnowledgeScope` 由 Profile 固定，约束可见 Library 和必须具备的标签。
5. `ScopedKnowledgeProvider` 在搜索和直接按 ID 读取两条路径上都执行范围检查，防止模型绕过搜索
   猜测文档 ID。
6. Agent 永远只读长期知识；创建、导入、发布和归档是人类知识管理界面的独立权限域。

流程知识不进入上述 Chunk 索引。`skills` Capability Pack 将已发布、Profile 可见的名称和描述作为
轻量目录提供给模型，只有任务匹配时才通过 `load_skill` 读取完整正文。具体格式见
[`skills.md`](skills.md)。

当前文件系统 Provider 把所有受管文档映射到稳定的 `default` Library，标签和 Library 范围都会在
搜索与直接读取上执行。等 V2.1 Library/Source 数据模型落地后可增加更多 Library id，不需要改变
Profile 或 Agent 工具契约。

## 新增专用 Agent

1. 先定义业务对象、成功标准和权限，而不是先写 Prompt。
2. 组合已有 Capability Pack；只有领域动作缺失时才增加 Pack。
3. 为知识 Agent 定义 `KnowledgeScope`，不要在 Prompt 中用自然语言模拟权限。
4. 分配新的 Profile id/version，声明 UI Feature。
5. 增加工具集合、权限矩阵、知识范围和端到端任务测试。
6. 新 Profile 上线不改变已有会话的组合。

## 实例：客服 Agent

内置 `support` Profile 展示了“专用 Agent 不只是换 Prompt”：

```text
关系数据库（商品/库存/订单项/物流）
  -> 按任务加载 order-delivery-status / after-sales-resolution 等 Skill
  -> find_my_orders（认证顾客 + 时间/商品/状态线索）
  -> lookup_my_order（公开订单号）
  -> support KnowledgeScope 检索政策
  -> assess_after_sales_options 联查订单、物流、库存与政策规则
  -> 生成解释/下一步
  -> create_support_case（逐次批准）
  -> 本地持久化人工工单
```

顾客身份来自 `X-Customer-ID` 的认证上下文，不是模型参数；工具只能查询当前
`tenant_id + customer_id`。数据库区分内部主键和公开 `order_number`/`case_number`，Agent 与用户
不接触内部 ID。退款和换货方案由普通代码联查 `customers`、`products`、`inventory`、
`commerce_orders`、`commerce_order_items`、`shipments` 与 `payment_transactions` 后计算，LLM
无权覆盖。可用库存固定为
`on_hand - reserved`，变化原因保存在 `inventory_movements`。Agent 不接触支付凭据，也不能执行
退款或库存扣减；唯一写动作是在数据库事务中创建租户隔离 `after_sales_cases`，工具策略强制审批
和独占执行。客服知识独立使用 `support` 标签 Scope，猜测其他知识文档 ID 也会被 Provider 拒绝。

前端把 Skill 选择显示为可审计流程卡：加载中、已加载/失败、版本、revision、标签和可展开的实际
流程正文；随后继续展示数据库工具与审批调用。它不显示或伪装模型隐藏思维链。

开发启动会为 `SUPPORT_DEMO_TENANT_ID` 幂等插入三组关系数据：规则内可退且有换货库存、退款过期
且无换货库存、运输中。人类可读清单位于
[`../examples/support/demo-data.json`](../examples/support/demo-data.json)，运行时事实来源始终是数据库。

## 下一阶段

1. 让中断恢复和消息 projection 完全由 append-only events 派生。
2. 增加 Token 成本、延迟分位和 Profile 任务成功率评测。
3. Knowledge V2.1 多 Library/Source 元数据和原生安全导入。
4. 数据量与检索评测证明必要后再增加向量召回和重排。

统一 Tool Policy 以及事件/Prompt/Tool Schema 快照基础已经完成。可执行的检索基线也已提供：设置
开发/生产默认启用 SQLite FTS5 持久化增量索引；
运行 `python tools/evaluate_knowledge.py evals/knowledge.sample.jsonl` 会输出 Hit Rate、MRR 和逐案例
排名。新增专用知识 Agent 时应提交自己的 JSONL 评测集，而不是只调整 Prompt。

当前不引入动态插件市场、多 Agent、自修改运行时或任意 Shell。这些能力必须建立在 Profile、
Tool Policy、事件日志和沙箱稳定之后。
