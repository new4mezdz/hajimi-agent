---
id: system/knowledge-base-overview
title: 单 Agent 知识库系统概览
summary: 当前知识库系统的目标、边界和第一阶段技术决策
tags: [agent, knowledge-base, architecture]
status: active
---

# 单 Agent 知识库系统概览

## 当前目标

第一阶段只构建一个 Agent。系统需要能够从本地、版本化的知识文档中检索证据，
在回答中保留来源，并在没有证据时明确说明知识库尚未覆盖该问题。

## 当前边界

知识源使用 `knowledge/` 下的 Markdown 或纯文本文件。检索采用本地关键词与短语排序，
暂不引入子 Agent、MCP Server、外部向量数据库、复杂权限模型和自动写入记忆。

## 演进原则

先验证知识格式、检索质量和引用闭环。知识量或团队规模增长后，可以在保持现有查询接口的
前提下加入向量检索、重排、权限过滤和 MCP 适配层。
