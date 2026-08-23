---
id: system/knowledge-base-overview
title: Agent Profile 知识库系统概览
summary: 当前知识库系统的目标、Profile 范围、索引边界和演进原则
tags: [agent, knowledge-base, architecture]
status: active
---

# Agent Profile 知识库系统概览

## 当前目标

系统通过多个 Agent Profile 共用同一知识 Runtime。只有声明 `knowledge` Capability Pack 和
`KnowledgeScope` 的 Profile 可以检索知识；系统需要保留来源，并在没有证据时明确说明当前
Profile 的知识范围尚未覆盖该问题。

## 当前边界

知识源使用 `knowledge/` 下的 Markdown 或纯文本文件。原文是事实来源，Chunk 和索引是可重建
派生数据。索引可以使用内存词法实现或 SQLite FTS5；Profile Scope 在索引之外同时约束搜索和
直接文档读取。Agent 不能自动写入、发布或归档长期知识。

## 演进原则

先用固定 JSONL 问题集验证召回率、MRR 和引用闭环。知识量或团队规模增长后，可以在保持
`KnowledgeProvider` 查询接口的前提下加入 Library/Source、向量召回、重排和 MCP 适配；向量库
始终只是索引，不拥有原文生命周期或权限。
