---
id: knowledge-authoring-guide
title: 知识库编写说明
status: excluded
tags: [knowledge-base, authoring]
---

# 知识库编写说明

`knowledge/` 是单 Agent 的版本化知识源。当前支持 UTF-8 编码的 `.md` 和 `.txt` 文件，
子目录可以按领域自由组织。`README.md` 标记为 `excluded`，因此不会进入检索结果。

推荐格式：

```markdown
---
id: product/refund-policy
title: 退款政策
summary: 面向客服和订单系统的有效退款规则
tags: [product, policy, refund]
status: active
---

# 退款政策

正文内容……
```

字段说明：

- `id`：稳定且唯一；允许字母、数字、中文以及 `- _ . /`。省略时使用相对文件路径。
- `title`：展示标题；省略时使用正文第一个标题或文件名。
- `summary`：一句话说明文档用途，会参与检索。
- `tags`：逗号分隔的标签列表，可用于过滤。
- `status`：`active`、`published`、`draft`、`archived` 或 `excluded`。只有前两种会被检索。

编写原则：

- 一份文档只表达一个稳定主题。
- 重要事实写明适用范围、生效时间和例外条件。
- 修改规则时直接更新原文；不要保留互相冲突的重复版本。
- 不要存放密码、Token、私钥或不应被所有用户读取的数据。
