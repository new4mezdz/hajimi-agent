"use client";

import {
  Archive,
  ArrowLeft,
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  CloudUpload,
  FilePlus2,
  FileText,
  FlaskConical,
  LoaderCircle,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Tag,
} from "lucide-react";
import Link from "next/link";
import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const TENANT_ID = "local";

type DocumentStatus = "draft" | "active" | "published" | "archived" | "excluded";
type FilterStatus = "all" | "published" | "draft" | "archived";

type KnowledgeDocumentSummary = {
  document_id: string;
  title: string;
  summary: string;
  tags: string[];
  status: DocumentStatus;
  source: string;
  updated_at: string;
  revision: string;
};

type KnowledgeDocument = KnowledgeDocumentSummary & {
  body: string;
  source_uri?: string | null;
  action?: string | null;
};

type DocumentDraft = {
  documentId: string;
  title: string;
  summary: string;
  tagsText: string;
  status: DocumentStatus;
  body: string;
  revision: string | null;
};

type SearchHit = {
  document_id: string;
  title: string;
  section: string;
  snippet: string;
  source: string;
  line_start: number;
  line_end: number;
  score: number;
  tags: string[];
  citation: string;
};

const EMPTY_DRAFT: DocumentDraft = {
  documentId: "",
  title: "",
  summary: "",
  tagsText: "",
  status: "draft",
  body: "# 新知识文档\n\n从这里开始编写内容。",
  revision: null,
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  draft: "草稿",
  active: "已发布",
  published: "已发布",
  archived: "已归档",
  excluded: "不参与检索",
};

function requestHeaders(contentType = false): HeadersInit {
  return {
    ...(contentType ? { "Content-Type": "application/json" } : {}),
    "X-Tenant-ID": TENANT_ID,
  };
}

function documentPath(documentId: string) {
  return documentId.split("/").map(encodeURIComponent).join("/");
}

async function readApiError(response: Response, fallback: string) {
  const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
  return payload?.detail ?? fallback;
}

function toDraft(document: KnowledgeDocument): DocumentDraft {
  return {
    documentId: document.document_id,
    title: document.title,
    summary: document.summary,
    tagsText: document.tags.join(", "),
    status: document.status,
    body: document.body,
    revision: document.revision,
  };
}

function slugFromFilename(filename: string) {
  return filename
    .replace(/\.(md|markdown|txt)$/i, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/[^\p{L}\p{N}._/-]+/gu, "-")
    .replace(/^-+|-+$/g, "") || "imported-document";
}

function parseImportedFile(filename: string, content: string): DocumentDraft {
  const normalized = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = normalized.split("\n");
  const metadata: Record<string, string> = {};
  let body = normalized;

  if (lines[0]?.trim() === "---") {
    const closingIndex = lines.slice(1).findIndex((line) => line.trim() === "---");
    if (closingIndex >= 0) {
      const actualClosingIndex = closingIndex + 1;
      for (const line of lines.slice(1, actualClosingIndex)) {
        const separator = line.indexOf(":");
        if (separator > 0) {
          metadata[line.slice(0, separator).trim().toLowerCase()] = line
            .slice(separator + 1)
            .trim()
            .replace(/^['"]|['"]$/g, "");
        }
      }
      body = lines.slice(actualClosingIndex + 1).join("\n").trim();
    }
  }

  const firstHeading = body.match(/^#\s+(.+)$/m)?.[1]?.trim();
  const tags = (metadata.tags ?? "")
    .replace(/^\[|\]$/g, "")
    .split(",")
    .map((tag) => tag.trim().replace(/^['"]|['"]$/g, ""))
    .filter(Boolean);

  return {
    documentId: metadata.id || slugFromFilename(filename),
    title: metadata.title || firstHeading || slugFromFilename(filename),
    summary: metadata.summary || "",
    tagsText: tags.join(", "),
    status: "draft",
    body: body || `# ${firstHeading || slugFromFilename(filename)}\n`,
    revision: null,
  };
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DocumentDraft>(EMPTY_DRAFT);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [listQuery, setListQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [testQuery, setTestQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDocuments = useCallback(async (preferredId?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/knowledge/manage/documents`, {
        headers: requestHeaders(),
      });
      if (!response.ok) throw new Error(await readApiError(response, "知识文档加载失败"));
      const loaded = (await response.json()) as KnowledgeDocumentSummary[];
      setDocuments(loaded);
      const nextId = preferredId ?? selectedId ?? loaded[0]?.document_id ?? null;
      if (nextId && loaded.some((document) => document.document_id === nextId)) {
        setSelectedId(nextId);
      } else if (!nextId) {
        setDraft(EMPTY_DRAFT);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "知识文档加载失败");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    let cancelled = false;
    async function initialLoad() {
      try {
        const response = await fetch(`${API_BASE}/v1/knowledge/manage/documents`, {
          headers: requestHeaders(),
        });
        if (!response.ok) throw new Error(await readApiError(response, "知识文档加载失败"));
        const loaded = (await response.json()) as KnowledgeDocumentSummary[];
        if (!cancelled) {
          setDocuments(loaded);
          setSelectedId(loaded[0]?.document_id ?? null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "知识文档加载失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initialLoad();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    async function loadDocument() {
      setDocumentLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `${API_BASE}/v1/knowledge/manage/documents/${documentPath(selectedId!)}`,
          { headers: requestHeaders() },
        );
        if (!response.ok) throw new Error(await readApiError(response, "知识文档读取失败"));
        const loaded = (await response.json()) as KnowledgeDocument;
        if (!cancelled) {
          setDraft(toDraft(loaded));
          setDirty(false);
          setPreview(false);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "知识文档读取失败");
        }
      } finally {
        if (!cancelled) setDocumentLoading(false);
      }
    }
    void loadDocument();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const filteredDocuments = useMemo(() => {
    const normalizedQuery = listQuery.trim().toLowerCase();
    return documents.filter((document) => {
      const matchesStatus =
        filter === "all" ||
        (filter === "published" && ["active", "published"].includes(document.status)) ||
        (filter === "draft" && document.status === "draft") ||
        (filter === "archived" && ["archived", "excluded"].includes(document.status));
      const matchesQuery =
        !normalizedQuery ||
        document.title.toLowerCase().includes(normalizedQuery) ||
        document.document_id.toLowerCase().includes(normalizedQuery) ||
        document.tags.some((tag) => tag.toLowerCase().includes(normalizedQuery));
      return matchesStatus && matchesQuery;
    });
  }, [documents, filter, listQuery]);

  const counts = useMemo(() => ({
    all: documents.length,
    published: documents.filter((document) => ["active", "published"].includes(document.status)).length,
    draft: documents.filter((document) => document.status === "draft").length,
    archived: documents.filter((document) => ["archived", "excluded"].includes(document.status)).length,
  }), [documents]);

  function updateDraft(patch: Partial<DocumentDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
    setNotice(null);
  }

  function startNewDocument() {
    if (dirty && !window.confirm("当前修改尚未保存，确定新建文档吗？")) return;
    setSelectedId(null);
    setDraft(EMPTY_DRAFT);
    setDirty(false);
    setPreview(false);
    setError(null);
    setNotice("已创建空白草稿，保存后会写入知识目录。 ");
  }

  function chooseDocument(documentId: string) {
    if (documentId === selectedId) return;
    if (dirty && !window.confirm("当前修改尚未保存，确定切换文档吗？")) return;
    setSelectedId(documentId);
    setNotice(null);
  }

  async function saveDocument(nextStatus?: DocumentStatus) {
    const documentId = draft.documentId.trim();
    const title = draft.title.trim();
    if (!documentId || !title) {
      setError("文档 ID 和标题不能为空。");
      return;
    }
    const status = nextStatus ?? draft.status;
    setSaving(true);
    setError(null);
    setNotice(null);
    const payload = {
      document_id: documentId,
      title,
      summary: draft.summary.trim(),
      tags: draft.tagsText.split(",").map((tag) => tag.trim()).filter(Boolean),
      status,
      body: draft.body,
      expected_revision: draft.revision,
    };
    const editing = draft.revision !== null;
    const endpoint = editing
      ? `${API_BASE}/v1/knowledge/manage/documents/${documentPath(documentId)}`
      : `${API_BASE}/v1/knowledge/manage/documents`;
    try {
      const response = await fetch(endpoint, {
        method: editing ? "PUT" : "POST",
        headers: requestHeaders(true),
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readApiError(response, "文档保存失败"));
      const saved = (await response.json()) as KnowledgeDocument;
      setDraft(toDraft(saved));
      setSelectedId(saved.document_id);
      setDirty(false);
      setNotice(status === "active" ? "文档已发布，Agent 现在可以检索到它。" : "文档已保存。");
      await loadDocuments(saved.document_id);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "文档保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (dirty && !window.confirm("导入会替换当前未保存内容，确定继续吗？")) return;
    try {
      const content = await file.text();
      setSelectedId(null);
      setDraft(parseImportedFile(file.name, content));
      setDirty(true);
      setPreview(false);
      setError(null);
      setNotice(`已导入 ${file.name}，检查元数据后保存。`);
    } catch {
      setError("文件读取失败，请导入 UTF-8 Markdown 或纯文本文件。");
    }
  }

  async function testRetrieval(event?: FormEvent) {
    event?.preventDefault();
    const query = testQuery.trim();
    if (!query) return;
    setSearching(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/knowledge/search`, {
        method: "POST",
        headers: requestHeaders(true),
        body: JSON.stringify({ query, limit: 5, tags: [] }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "检索测试失败"));
      const payload = (await response.json()) as { results: SearchHit[] };
      setSearchResults(payload.results);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "检索测试失败");
    } finally {
      setSearching(false);
    }
  }

  return (
    <main className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brandRow}>
          <span className={styles.brandIcon}><BookOpen size={19} /></span>
          <div><strong>知识管理</strong><small>单 Agent 知识库</small></div>
        </div>

        <Link className={styles.backLink} href="/">
          <ArrowLeft size={15} /> 返回 Agent
        </Link>

        <button className={styles.newButton} type="button" onClick={startNewDocument}>
          <FilePlus2 size={16} /> 新建文档
        </button>
        <button className={styles.importButton} type="button" onClick={() => fileInputRef.current?.click()}>
          <CloudUpload size={15} /> 导入文件
        </button>
        <input
          ref={fileInputRef}
          className={styles.hiddenInput}
          type="file"
          accept=".md,.markdown,.txt,text/markdown,text/plain"
          onChange={(event) => void importFile(event)}
        />

        <div className={styles.navLabel}>文档状态</div>
        <nav className={styles.filters} aria-label="知识文档筛选">
          {([
            ["all", "全部文档"],
            ["published", "已发布"],
            ["draft", "草稿"],
            ["archived", "已归档"],
          ] as [FilterStatus, string][]).map(([id, label]) => (
            <button
              key={id}
              className={filter === id ? styles.filterActive : ""}
              type="button"
              onClick={() => setFilter(id)}
            >
              <span>{label}</span><em>{counts[id]}</em>
            </button>
          ))}
        </nav>

        <div className={styles.sidebarFoot}>
          <Sparkles size={14} />
          <span>只有已发布文档会进入 Agent 检索。</span>
        </div>
      </aside>

      <section className={styles.library}>
        <header className={styles.libraryHeader}>
          <div><span>KNOWLEDGE</span><h1>知识文档</h1></div>
          <button type="button" onClick={() => void loadDocuments(selectedId)} aria-label="刷新文档列表">
            <RefreshCw size={15} />
          </button>
        </header>
        <label className={styles.listSearch}>
          <Search size={15} />
          <input value={listQuery} onChange={(event) => setListQuery(event.target.value)} placeholder="搜索标题、ID 或标签" />
        </label>

        <div className={styles.documentList}>
          {loading ? (
            <div className={styles.emptyList}><LoaderCircle className={styles.spin} size={20} />正在读取知识目录</div>
          ) : filteredDocuments.length ? filteredDocuments.map((document) => (
            <button
              key={document.document_id}
              className={selectedId === document.document_id ? styles.documentActive : styles.documentCard}
              type="button"
              onClick={() => chooseDocument(document.document_id)}
            >
              <span className={styles.fileIcon}><FileText size={16} /></span>
              <span className={styles.documentCopy}>
                <strong>{document.title}</strong>
                <small>{document.document_id}</small>
                <span className={styles.documentMeta}>
                  <em data-status={document.status}>{STATUS_LABELS[document.status]}</em>
                  <time>{new Date(document.updated_at).toLocaleDateString("zh-CN")}</time>
                </span>
              </span>
              <ChevronRight size={14} />
            </button>
          )) : (
            <div className={styles.emptyList}>当前筛选下没有文档</div>
          )}
        </div>
      </section>

      <section className={styles.editor}>
        <header className={styles.editorHeader}>
          <div>
            <span>{draft.revision ? "编辑文档" : "新建文档"}</span>
            <h2>{draft.title || "未命名知识"}</h2>
          </div>
          <div className={styles.editorActions}>
            {draft.revision && draft.status !== "archived" ? (
              <button className={styles.archiveButton} type="button" disabled={saving} onClick={() => void saveDocument("archived")}>
                <Archive size={14} /> 归档
              </button>
            ) : null}
            <button className={styles.saveButton} type="button" disabled={saving} onClick={() => void saveDocument()}>
              {saving ? <LoaderCircle className={styles.spin} size={14} /> : <Save size={14} />}
              保存
            </button>
            {draft.status !== "active" ? (
              <button className={styles.publishButton} type="button" disabled={saving} onClick={() => void saveDocument("active")}>
                <Check size={14} /> 发布
              </button>
            ) : null}
          </div>
        </header>

        {error ? <div className={styles.errorBanner}><CircleAlert size={15} />{error}</div> : null}
        {notice ? <div className={styles.noticeBanner}><Check size={15} />{notice}</div> : null}

        <div className={styles.metadataGrid}>
          <label><span>文档 ID</span><input value={draft.documentId} disabled={Boolean(draft.revision)} onChange={(event) => updateDraft({ documentId: event.target.value })} placeholder="product/refund-policy" /></label>
          <label><span>标题</span><input value={draft.title} onChange={(event) => updateDraft({ title: event.target.value })} placeholder="文档标题" /></label>
          <label className={styles.summaryField}><span>摘要</span><input value={draft.summary} onChange={(event) => updateDraft({ summary: event.target.value })} placeholder="一句话说明这份知识用于什么场景" /></label>
          <label><span><Tag size={12} /> 标签</span><input value={draft.tagsText} onChange={(event) => updateDraft({ tagsText: event.target.value })} placeholder="product, policy, refund" /></label>
          <label><span>状态</span><select value={draft.status} onChange={(event) => updateDraft({ status: event.target.value as DocumentStatus })}>
            <option value="draft">草稿</option><option value="active">已发布</option><option value="archived">已归档</option><option value="excluded">不参与检索</option>
          </select></label>
        </div>

        <div className={styles.contentHeader}>
          <div><strong>正文</strong><span>{draft.body.length.toLocaleString()} 字符{dirty ? " · 未保存" : ""}</span></div>
          <div className={styles.viewSwitch}>
            <button className={!preview ? styles.viewActive : ""} type="button" onClick={() => setPreview(false)}>编辑</button>
            <button className={preview ? styles.viewActive : ""} type="button" onClick={() => setPreview(true)}>预览</button>
          </div>
        </div>
        <div className={styles.contentArea}>
          {documentLoading ? (
            <div className={styles.editorLoading}><LoaderCircle className={styles.spin} size={22} />正在加载文档</div>
          ) : preview ? (
            <article className={styles.markdown}><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft.body}</ReactMarkdown></article>
          ) : (
            <textarea value={draft.body} onChange={(event) => updateDraft({ body: event.target.value })} spellCheck={false} aria-label="知识文档正文" />
          )}
        </div>
      </section>

      <aside className={styles.inspector}>
        <div className={styles.inspectorHeading}>
          <span className={styles.testIcon}><FlaskConical size={16} /></span>
          <div><strong>检索测试</strong><small>验证 Agent 能否找到正确段落</small></div>
        </div>
        <form className={styles.testForm} onSubmit={(event) => void testRetrieval(event)}>
          <textarea value={testQuery} onChange={(event) => setTestQuery(event.target.value)} placeholder="例如：退款多久到账？" rows={3} />
          <button type="submit" disabled={searching || !testQuery.trim()}>
            {searching ? <LoaderCircle className={styles.spin} size={14} /> : <Search size={14} />}
            开始检索
          </button>
        </form>
        <div className={styles.testNote}>检索测试只会命中已发布文档；尚未保存的编辑内容不会参与。</div>

        <div className={styles.results}>
          {searchResults.length ? searchResults.map((result, index) => (
            <article key={`${result.document_id}-${result.line_start}`} className={styles.resultCard}>
              <header><span>#{index + 1}</span><em>{result.score.toFixed(2)}</em></header>
              <strong>{result.title}</strong>
              <small>{result.section}</small>
              <p>{result.snippet}</p>
              <footer>{result.source}:L{result.line_start}-L{result.line_end}</footer>
            </article>
          )) : (
            <div className={styles.emptyResults}>
              <Search size={21} />
              <strong>等待测试问题</strong>
              <span>结果会显示命中章节、片段、分数与来源行号。</span>
            </div>
          )}
        </div>
      </aside>
    </main>
  );
}
