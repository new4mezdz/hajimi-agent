"use client";

import { useChat } from "@ai-sdk/react";
import {
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
  type UIMessage,
} from "ai";
import Image from "next/image";
import Link from "next/link";
import {
  ArrowUp,
  BookOpen,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  FileDiff,
  FolderCode,
  FolderOpen,
  FileText,
  History,
  Info,
  GitBranch,
  GitCommitHorizontal,
  KeyRound,
  LoaderCircle,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  UploadCloud,
  WandSparkles,
  Wrench,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentBrand } from "./brand";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const TENANT_ID = "local";
const CONVERSATION_STORAGE_KEY = "agent-product-conversation-id";

const suggestions = [
  { icon: Search, label: "概览这个代码仓库的结构" },
  { icon: FileText, label: "找出项目的启动入口" },
  { icon: WandSparkles, label: "分析当前架构并给出改进建议" },
];

type WorkspaceInfo = {
  id: string;
  name: string;
  path: string;
};

type GitFileChange = {
  path: string;
  previous_path: string | null;
  status: string;
  staged: boolean;
  unstaged: boolean;
  additions: number;
  deletions: number;
  binary: boolean;
  diff: string;
  diff_truncated: boolean;
};

type ReviewCheck = {
  id: string;
  name: string;
  kind: "integrity" | "test" | "lint" | "build";
  status: "passed" | "failed" | "not_run";
  summary: string;
  output: string;
};

type GitReview = {
  repository: string;
  branch: string | null;
  head: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  clean: boolean;
  files: GitFileChange[];
  additions: number;
  deletions: number;
  restricted_changes: number;
  diff_truncated: boolean;
  checks: ReviewCheck[];
};

type GitConfirmation = {
  confirmation_id: string;
  action: "commit" | "push";
  title: string;
  details: string[];
  expires_at: string;
};

type ProviderId = "openai" | "deepseek" | "anthropic";

type AgentSettings = {
  provider: ProviderId;
  model: string;
  webSearchEnabled: boolean;
  workspaceWriteEnabled: boolean;
  agentInstructions: string;
  apiKeyConfigured: boolean;
  configuredProviders: ProviderId[];
  secureStorage: boolean;
};

type AgentSettingsInput = Omit<
  AgentSettings,
  "apiKeyConfigured" | "configuredProviders" | "secureStorage"
> & {
  apiKey: string | null;
  clearApiKey: boolean;
};

const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  provider: "openai",
  model: "openai:gpt-4.1-mini",
  webSearchEnabled: true,
  workspaceWriteEnabled: true,
  agentInstructions:
    "You are 哈基米sama, a reliable local coding agent. Be concise, accurate, inspect the workspace before making code claims, and use tools when useful.",
  apiKeyConfigured: false,
  configuredProviders: [],
  secureStorage: false,
};

const PROVIDERS: Record<
  ProviderId,
  { name: string; description: string; defaultModel: string; keyPlaceholder: string }
> = {
  openai: {
    name: "OpenAI",
    description: "GPT 系列模型",
    defaultModel: "openai:gpt-4.1-mini",
    keyPlaceholder: "sk-…",
  },
  deepseek: {
    name: "DeepSeek",
    description: "DeepSeek 对话与推理模型",
    defaultModel: "deepseek:deepseek-v4-flash",
    keyPlaceholder: "sk-…",
  },
  anthropic: {
    name: "Anthropic",
    description: "Claude 系列模型",
    defaultModel: "anthropic:claude-sonnet-4-5",
    keyPlaceholder: "sk-ant-…",
  },
};

function newConversationId() {
  return crypto.randomUUID();
}

export default function Home() {
  const [conversationId, setConversationId] = useState(() => {
    if (typeof window === "undefined") return "00000000-0000-4000-8000-000000000000";
    return window.localStorage.getItem(CONVERSATION_STORAGE_KEY) ?? newConversationId();
  });
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [agentSettings, setAgentSettings] = useState(DEFAULT_AGENT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [settingsAvailable, setSettingsAvailable] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  useEffect(() => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, conversationId);
  }, [conversationId]);

  useEffect(() => {
    function openSettings(event: globalThis.KeyboardEvent) {
      if (event.ctrlKey && event.key === ",") {
        event.preventDefault();
        setSettingsOpen(true);
      }
    }
    window.addEventListener("keydown", openSettings);
    return () => window.removeEventListener("keydown", openSettings);
  }, []);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;

    async function loadAgentSettings() {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        setSettingsAvailable(true);
        setAgentSettings(await invoke<AgentSettings>("get_agent_settings"));
      } catch (error) {
        setSettingsError(error instanceof Error ? error.message : String(error));
      }
    }

    void loadAgentSettings();
  }, []);

  function startNewConversation() {
    const id = newConversationId();
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, id);
    setConversationId(id);
  }

  async function registerWorkspace(path: string) {
    const response = await fetch(`${API_BASE}/v1/workspaces`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Tenant-ID": TENANT_ID,
      },
      body: JSON.stringify({ path }),
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(payload?.detail ?? "工作区连接失败");
    }
    return (await response.json()) as WorkspaceInfo;
  }

  async function selectWorkspace() {
    setWorkspaceError(null);
    let selectedPath: string | null = null;
    try {
      if ("__TAURI_INTERNALS__" in window) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const selected = await open({
          directory: true,
          multiple: false,
          title: "选择代码仓库",
        });
        selectedPath = typeof selected === "string" ? selected : null;
      } else {
        selectedPath = window.prompt("开发模式：请输入代码仓库的绝对路径");
      }
      if (!selectedPath) return;

      setWorkspaceLoading(true);
      setWorkspace(await registerWorkspace(selectedPath));
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "工作区连接失败");
    } finally {
      setWorkspaceLoading(false);
    }
  }

  async function saveAgentSettings(input: AgentSettingsInput) {
    if (!settingsAvailable) {
      throw new Error("快速配置只在桌面客户端中可用；浏览器开发模式请使用 .env。");
    }
    const { invoke } = await import("@tauri-apps/api/core");
    const saved = await invoke<AgentSettings>("save_agent_settings", { input });

    let online = false;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        online = (await fetch(`${API_BASE}/health/ready`)).ok;
      } catch {
        online = false;
      }
      if (online) break;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    if (!online) throw new Error("设置已保存，但本地 Agent 服务未能重新连接。");

    if (workspace) {
      setWorkspace(await registerWorkspace(workspace.path));
    }
    setAgentSettings(saved);
    setSettingsError(null);
    return saved;
  }

  return (
    <main className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}>
        <div className="sidebar-topline">
          <div className="brand-mark" aria-hidden="true">
            <Image className="agent-image" src={agentBrand.iconSrc} alt="" width={38} height={38} priority />
          </div>
          <div className="brand-copy">
            <span className="brand-name">{agentBrand.name}</span>
            <span className="brand-caption">{agentBrand.caption}</span>
          </div>
          <button
            className="icon-button sidebar-toggle"
            type="button"
            onClick={() => setSidebarOpen((value) => !value)}
            aria-label="切换侧栏"
          >
            {sidebarOpen ? <PanelLeftClose size={17} /> : <Menu size={17} />}
          </button>
        </div>

        <button className="new-chat-button" type="button" onClick={startNewConversation}>
          <MessageSquarePlus size={17} />
          <span>新建对话</span>
          <kbd>⌘ K</kbd>
        </button>

        <Link className="knowledge-nav-link" href="/knowledge">
          <BookOpen size={16} />
          <span>知识管理</span>
        </Link>

        <div className="sidebar-section workspace-section">
          <div className="section-label">
            <FolderCode size={13} />
            <span>代码仓库</span>
          </div>
          <button className="workspace-card" type="button" onClick={() => void selectWorkspace()}>
            <span className="workspace-card-icon">
              <FolderOpen size={16} />
            </span>
            <span className="workspace-card-copy">
              <strong>{workspace?.name ?? "打开本地仓库"}</strong>
              <small>
                {workspace?.path ?? (agentSettings.workspaceWriteEnabled ? "受控读写" : "只读模式")}
              </small>
            </span>
          </button>
          {workspaceError ? <p className="workspace-error">{workspaceError}</p> : null}
        </div>

        <div className="sidebar-section">
          <div className="section-label">
            <History size={13} />
            <span>最近</span>
          </div>
          <button className="history-item history-item-active" type="button">
            <span className="history-title">当前产品构想</span>
            <span className="history-time">刚刚</span>
          </button>
          <button className="history-item" type="button">
            <span className="history-title">Agent 能力规划</span>
            <span className="history-time">示例</span>
          </button>
        </div>

        <div className="sidebar-footer">
          <div className="usage-card">
            <div className="usage-row">
              <span>本月用量</span>
              <span>开发模式</span>
            </div>
            <div className="usage-track">
              <span />
            </div>
          </div>
          <button className="profile-row settings-trigger" type="button" onClick={() => setSettingsOpen(true)}>
            <div className="profile-avatar">
              <Settings size={15} />
            </div>
            <div className="profile-copy">
              <strong>设置</strong>
              <span>{PROVIDERS[agentSettings.provider].name} · {agentSettings.model.split(":").at(-1)}</span>
            </div>
            <span className="settings-shortcut">Ctrl ,</span>
          </button>
        </div>
      </aside>

      <section className="workspace">
        <ChatWorkspace
          key={conversationId}
          conversationId={conversationId}
          onNewConversation={startNewConversation}
          workspace={workspace}
          workspaceLoading={workspaceLoading}
          onSelectWorkspace={selectWorkspace}
          model={agentSettings.model}
          workspaceWriteEnabled={agentSettings.workspaceWriteEnabled}
          onOpenReview={() => {
            if (workspace) setReviewOpen(true);
            else void selectWorkspace();
          }}
        />
      </section>
      {settingsOpen ? (
        <SettingsDialog
          open
          settings={agentSettings}
          available={settingsAvailable}
          loadError={settingsError}
          onClose={() => setSettingsOpen(false)}
          onSave={saveAgentSettings}
        />
      ) : null}
      {reviewOpen && workspace ? (
        <ReviewDialog workspace={workspace} onClose={() => setReviewOpen(false)} />
      ) : null}
    </main>
  );
}

function ReviewDialog({ workspace, onClose }: { workspace: WorkspaceInfo; onClose: () => void }) {
  const [review, setReview] = useState<GitReview | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [confirmation, setConfirmation] = useState<GitConfirmation | null>(null);
  const [operationLoading, setOperationLoading] = useState(false);
  const [operationMessage, setOperationMessage] = useState<string | null>(null);

  const gitRequest = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${API_BASE}/v1/workspaces/${workspace.id}/git${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Tenant-ID": TENANT_ID,
        ...init?.headers,
      },
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(payload?.detail ?? "Git 操作失败");
    }
    return (await response.json()) as T;
  }, [workspace.id]);

  const applyReview = useCallback((result: GitReview) => {
    setReview(result);
    setSelectedPath((current) => (
      current && result.files.some((file) => file.path === current)
        ? current
        : result.files[0]?.path ?? null
    ));
  }, []);

  const loadReview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applyReview(await gitRequest<GitReview>("/review"));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }, [applyReview, gitRequest]);

  useEffect(() => {
    let active = true;
    gitRequest<GitReview>("/review")
      .then((result) => {
        if (active) applyReview(result);
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : String(loadError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [applyReview, gitRequest]);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !operationLoading) {
        if (confirmation) setConfirmation(null);
        else onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmation, onClose, operationLoading]);

  async function prepareCommit() {
    setOperationLoading(true);
    setError(null);
    setOperationMessage(null);
    try {
      setConfirmation(await gitRequest<GitConfirmation>("/commit/prepare", {
        method: "POST",
        body: JSON.stringify({ message: commitMessage }),
      }));
    } catch (operationError) {
      setError(operationError instanceof Error ? operationError.message : String(operationError));
    } finally {
      setOperationLoading(false);
    }
  }

  async function preparePush() {
    setOperationLoading(true);
    setError(null);
    setOperationMessage(null);
    try {
      setConfirmation(await gitRequest<GitConfirmation>("/push/prepare", { method: "POST" }));
    } catch (operationError) {
      setError(operationError instanceof Error ? operationError.message : String(operationError));
    } finally {
      setOperationLoading(false);
    }
  }

  async function confirmOperation() {
    if (!confirmation) return;
    setOperationLoading(true);
    setError(null);
    try {
      const path = confirmation.action === "commit" ? "/commit" : "/push";
      const result = await gitRequest<{ commit?: string; head?: string; subject?: string }>(path, {
        method: "POST",
        body: JSON.stringify({ confirmation_id: confirmation.confirmation_id }),
      });
      setOperationMessage(
        confirmation.action === "commit"
          ? `已创建提交 ${result.commit?.slice(0, 12)} · ${result.subject}`
          : `已推送 ${result.head?.slice(0, 12)}`,
      );
      if (confirmation.action === "commit") setCommitMessage("");
      setConfirmation(null);
      await loadReview();
    } catch (operationError) {
      setConfirmation(null);
      setError(operationError instanceof Error ? operationError.message : String(operationError));
    } finally {
      setOperationLoading(false);
    }
  }

  const selectedFile = review?.files.find((file) => file.path === selectedPath) ?? null;

  return (
    <div className="review-overlay" role="presentation">
      <section className="review-dialog" role="dialog" aria-modal="true" aria-labelledby="review-title">
        <header className="review-header">
          <div className="review-title-group">
            <div className="review-title-icon"><FileDiff size={18} /></div>
            <div>
              <span>CHANGE REVIEW</span>
              <h2 id="review-title">变更审查</h2>
            </div>
          </div>
          <div className="review-repository">
            <GitBranch size={14} />
            <strong>{review?.branch ?? "未识别分支"}</strong>
            {review?.upstream ? <span>{review.ahead} ahead · {review.behind} behind</span> : null}
          </div>
          <button className="review-refresh" type="button" onClick={() => void loadReview()} disabled={loading}>
            <RefreshCw className={loading ? "spinning" : ""} size={15} />
            刷新
          </button>
          <button className="settings-close" type="button" onClick={onClose} disabled={operationLoading} aria-label="关闭变更审查">
            <X size={17} />
          </button>
        </header>

        {loading && !review ? (
          <div className="review-loading"><LoaderCircle className="spinning" size={18} />正在读取 Git 变更</div>
        ) : error && !review ? (
          <div className="review-fatal">
            <CircleAlert size={26} />
            <h3>无法打开变更审查</h3>
            <p>{error}</p>
            <button type="button" onClick={() => void loadReview()}>重试</button>
          </div>
        ) : review ? (
          <div className="review-layout">
            <aside className="review-files-panel">
              <div className="review-summary">
                <div>
                  <strong>{review.files.length}</strong>
                  <span>修改文件</span>
                </div>
                <div className="review-line-stats">
                  <span className="line-add">+{review.additions}</span>
                  <span className="line-delete">−{review.deletions}</span>
                </div>
              </div>
              {review.restricted_changes ? (
                <div className="restricted-warning">
                  <CircleAlert size={13} />
                  {review.restricted_changes} 个敏感或忽略路径已隐藏，并会阻止提交
                </div>
              ) : null}
              <div className="review-file-list">
                {review.files.map((file) => (
                  <button
                    key={file.path}
                    className={selectedPath === file.path ? "review-file is-selected" : "review-file"}
                    type="button"
                    onClick={() => setSelectedPath(file.path)}
                  >
                    <span className={`file-status file-status-${file.status}`}>{file.status.slice(0, 1).toUpperCase()}</span>
                    <span className="review-file-copy">
                      <strong title={file.path}>{file.path.split("/").at(-1)}</strong>
                      <small>{file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : workspace.name}</small>
                    </span>
                    <span className="file-line-stats">
                      {file.additions ? <em>+{file.additions}</em> : null}
                      {file.deletions ? <i>−{file.deletions}</i> : null}
                    </span>
                    <ChevronRight size={12} />
                  </button>
                ))}
                {review.clean ? (
                  <div className="review-clean-state"><CircleCheck size={20} /><strong>工作树干净</strong><span>没有需要审查的变更</span></div>
                ) : null}
              </div>
            </aside>

            <main className="review-diff-panel">
              {selectedFile ? (
                <>
                  <div className="diff-file-header">
                    <div>
                      <FileText size={14} />
                      <strong>{selectedFile.path}</strong>
                    </div>
                    <span>{selectedFile.staged ? "已暂存" : "未暂存"}{selectedFile.unstaged && selectedFile.staged ? " + 工作区变更" : ""}</span>
                  </div>
                  <DiffPreview file={selectedFile} />
                </>
              ) : (
                <div className="diff-empty"><FileDiff size={28} /><span>选择文件以查看 Diff</span></div>
              )}
            </main>

            <aside className="review-actions-panel">
              <section className="review-checks">
                <div className="action-section-heading">
                  <span>检查与测试</span>
                  <small>{review.checks.filter((check) => check.status === "passed").length}/{review.checks.length} passed</small>
                </div>
                {review.checks.map((check) => (
                  <div key={check.id} className={`review-check check-${check.status}`}>
                    {check.status === "passed" ? <CircleCheck size={15} /> : <CircleAlert size={15} />}
                    <div>
                      <strong>{check.name}</strong>
                      <span>{check.summary}</span>
                    </div>
                  </div>
                ))}
                <p className="test-safety-note">项目测试需要执行仓库代码；在受控终端完成前，本页不会偷偷运行。</p>
              </section>

              <section className="commit-section">
                <div className="action-section-heading"><span>创建提交</span></div>
                <textarea
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                  placeholder="简要描述这次变更…"
                  rows={3}
                  maxLength={500}
                />
                <button
                  className="review-commit-button"
                  type="button"
                  disabled={review.clean || review.restricted_changes > 0 || !commitMessage.trim() || operationLoading}
                  onClick={() => void prepareCommit()}
                >
                  <GitCommitHorizontal size={14} />
                  审查并创建提交
                </button>
              </section>

              <section className="push-section">
                <div className="action-section-heading"><span>远端</span></div>
                <div className="push-target">
                  <GitBranch size={14} />
                  <div><strong>{review.upstream ?? `origin/${review.branch ?? "branch"}`}</strong><span>不会使用强制推送</span></div>
                </div>
                <button className="review-push-button" type="button" disabled={!review.head || operationLoading} onClick={() => void preparePush()}>
                  <UploadCloud size={14} />
                  审查并推送
                </button>
              </section>

              {operationMessage ? <div className="operation-success"><CircleCheck size={14} />{operationMessage}</div> : null}
              {error ? <div className="operation-error"><CircleAlert size={14} />{error}</div> : null}
            </aside>
          </div>
        ) : null}

        {confirmation ? (
          <div className="git-confirm-overlay" role="presentation">
            <div className="git-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
              <div className="confirm-icon">
                {confirmation.action === "commit" ? <GitCommitHorizontal size={20} /> : <UploadCloud size={20} />}
              </div>
              <span>需要你的明确确认</span>
              <h3 id="confirm-title">{confirmation.action === "commit" ? "创建这个 Git 提交？" : "推送这个分支？"}</h3>
              <ul>{confirmation.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
              <p>确认令牌只能使用一次，并会在两分钟后失效；仓库状态变化也会使它失效。</p>
              <div className="confirm-actions">
                <button type="button" onClick={() => setConfirmation(null)} disabled={operationLoading}>取消</button>
                <button className="confirm-primary" type="button" onClick={() => void confirmOperation()} disabled={operationLoading}>
                  {operationLoading ? <LoaderCircle className="spinning" size={14} /> : <Check size={14} />}
                  {confirmation.action === "commit" ? "确认创建提交" : "确认推送"}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function DiffPreview({ file }: { file: GitFileChange }) {
  if (file.binary) {
    return <div className="diff-empty"><FileText size={28} /><span>二进制文件不显示内容预览</span></div>;
  }
  if (!file.diff) {
    return <div className="diff-empty"><FileDiff size={28} /><span>这个文件没有可显示的文本 Diff</span></div>;
  }
  return (
    <pre className="diff-preview" aria-label={`${file.path} diff`}>
      <code>
        {file.diff.split("\n").map((line, index) => {
          const kind = line.startsWith("+") && !line.startsWith("+++")
            ? "diff-addition"
            : line.startsWith("-") && !line.startsWith("---")
              ? "diff-deletion"
              : line.startsWith("@@")
                ? "diff-hunk"
                : line.startsWith("diff --git") || line.startsWith("+++") || line.startsWith("---")
                  ? "diff-meta"
                  : "diff-context";
          return <span className={kind} key={`${index}-${line.slice(0, 20)}`}>{line || " "}</span>;
        })}
      </code>
    </pre>
  );
}

type SettingsTab = "model" | "agent" | "permissions" | "about";

function SettingsDialog({
  open,
  settings,
  available,
  loadError,
  onClose,
  onSave,
}: {
  open: boolean;
  settings: AgentSettings;
  available: boolean;
  loadError: string | null;
  onClose: () => void;
  onSave: (input: AgentSettingsInput) => Promise<AgentSettings>;
}) {
  const [tab, setTab] = useState<SettingsTab>("model");
  const [draft, setDraft] = useState<AgentSettingsInput>({
    ...settings,
    apiKey: null,
    clearApiKey: false,
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !saving) onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose, saving]);

  if (!open) return null;

  const keyConfigured = settings.configuredProviders.includes(draft.provider) && !draft.clearApiKey;

  function chooseProvider(provider: ProviderId) {
    setDraft((current) => ({
      ...current,
      provider,
      model: PROVIDERS[provider].defaultModel,
      apiKey: null,
      clearApiKey: false,
    }));
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      await onSave({
        ...draft,
        model: draft.model.trim(),
        agentInstructions: draft.agentInstructions.trim(),
        apiKey: draft.apiKey?.trim() || null,
      });
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  const tabs: { id: SettingsTab; label: string; icon: typeof Bot }[] = [
    { id: "model", label: "模型与 API", icon: KeyRound },
    { id: "agent", label: "Agent", icon: Bot },
    { id: "permissions", label: "权限", icon: ShieldCheck },
    { id: "about", label: "关于", icon: Info },
  ];

  return (
    <div className="settings-overlay" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !saving) onClose();
    }}>
      <form className="settings-dialog" onSubmit={handleSave} role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <aside className="settings-nav">
          <div className="settings-nav-heading">
            <div className="settings-logo"><Settings size={17} /></div>
            <div>
              <strong id="settings-title">设置</strong>
              <span>Hajimi Agent</span>
            </div>
          </div>
          <nav aria-label="设置分类">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                className={tab === id ? "is-active" : ""}
                type="button"
                onClick={() => setTab(id)}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </nav>
          <div className="settings-storage-note">
            <ShieldCheck size={14} />
            <span>{settings.secureStorage ? "密钥受 Windows DPAPI 保护" : "浏览器模式不保存密钥"}</span>
          </div>
        </aside>

        <div className="settings-main">
          <header className="settings-header">
            <div>
              <span>SETTINGS</span>
              <h2>{tabs.find((item) => item.id === tab)?.label}</h2>
            </div>
            <button className="settings-close" type="button" onClick={onClose} disabled={saving} aria-label="关闭设置">
              <X size={17} />
            </button>
          </header>

          <div className="settings-content">
            {tab === "model" ? (
              <section className="settings-panel">
                <div className="settings-section-heading">
                  <h3>模型提供商</h3>
                  <p>选择 Agent 使用的服务。保存后本地服务会自动重启。</p>
                </div>
                <div className="provider-grid">
                  {(Object.entries(PROVIDERS) as [ProviderId, (typeof PROVIDERS)[ProviderId]][]).map(([id, provider]) => (
                    <button
                      key={id}
                      className={draft.provider === id ? "provider-card is-selected" : "provider-card"}
                      type="button"
                      onClick={() => chooseProvider(id)}
                    >
                      <span className="provider-monogram">{provider.name.slice(0, 1)}</span>
                      <span>
                        <strong>{provider.name}</strong>
                        <small>{provider.description}</small>
                      </span>
                      {draft.provider === id ? <CheckCircle2 size={16} /> : null}
                    </button>
                  ))}
                </div>

                <label className="settings-field">
                  <span>模型 ID</span>
                  <input
                    value={draft.model}
                    onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
                    placeholder={PROVIDERS[draft.provider].defaultModel}
                    spellCheck={false}
                  />
                  <small>使用 `provider:model` 格式，也可以填入该提供商支持的其他模型。</small>
                </label>

                <label className="settings-field">
                  <span className="settings-field-title">
                    API Key
                    <em className={keyConfigured ? "key-status is-configured" : "key-status"}>
                      {keyConfigured ? "已安全保存" : "未在客户端保存"}
                    </em>
                  </span>
                  <input
                    type="password"
                    value={draft.apiKey ?? ""}
                    onChange={(event) => setDraft((current) => ({
                      ...current,
                      apiKey: event.target.value,
                      clearApiKey: false,
                    }))}
                    placeholder={keyConfigured ? "留空以保留当前密钥" : `${PROVIDERS[draft.provider].keyPlaceholder}（留空可沿用 .env）`}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <small>密钥不会显示、不会进入对话，也不会写入项目目录。</small>
                </label>
                {keyConfigured ? (
                  <button
                    className="clear-key-button"
                    type="button"
                    onClick={() => setDraft((current) => ({ ...current, apiKey: null, clearApiKey: true }))}
                  >
                    清除已保存的 {PROVIDERS[draft.provider].name} 密钥
                  </button>
                ) : null}

                <SettingToggle
                  title="网页搜索"
                  description="允许 Agent 在需要时使用配置好的搜索服务。"
                  checked={draft.webSearchEnabled}
                  onChange={(checked) => setDraft((current) => ({ ...current, webSearchEnabled: checked }))}
                />
              </section>
            ) : null}

            {tab === "agent" ? (
              <section className="settings-panel">
                <div className="settings-section-heading">
                  <h3>Agent 指令</h3>
                  <p>定义它的角色、回答习惯与长期工作方式。</p>
                </div>
                <label className="settings-field settings-field-grow">
                  <span>系统指令</span>
                  <textarea
                    value={draft.agentInstructions}
                    onChange={(event) => setDraft((current) => ({ ...current, agentInstructions: event.target.value }))}
                    rows={12}
                  />
                  <small>{draft.agentInstructions.length.toLocaleString()} / 20,000 字符</small>
                </label>
              </section>
            ) : null}

            {tab === "permissions" ? (
              <section className="settings-panel">
                <div className="settings-section-heading">
                  <h3>工作区权限</h3>
                  <p>第一版只开放文件读取、搜索和受控写入，不开放 Shell 命令。</p>
                </div>
                <div className="permission-card">
                  <div className="permission-icon"><FolderOpen size={17} /></div>
                  <div>
                    <strong>读取与搜索</strong>
                    <p>仅限你主动选择的工作区，路径越界会被确定性规则拒绝。</p>
                  </div>
                  <span className="always-on-badge">始终开启</span>
                </div>
                <SettingToggle
                  title="允许申请文件写入"
                  description="Agent 可以提出写入请求，但每次真正落盘前仍需要你批准。关闭后写入工具不会提供给模型。"
                  checked={draft.workspaceWriteEnabled}
                  onChange={(checked) => setDraft((current) => ({ ...current, workspaceWriteEnabled: checked }))}
                />
                <div className="security-callout">
                  <ShieldCheck size={17} />
                  <div>
                    <strong>确定性边界</strong>
                    <p>模型不能自行扩大权限。后端会校验工作区 ID、规范化路径，并阻止目录穿越与未批准写入。</p>
                  </div>
                </div>
              </section>
            ) : null}

            {tab === "about" ? (
              <section className="settings-panel about-panel">
                <div className="about-mark">
                  <Image className="agent-image" src={agentBrand.iconSrc} alt="" width={58} height={58} />
                </div>
                <h3>{agentBrand.name}</h3>
                <p>一个面向本地代码仓库的桌面 AI Agent。</p>
                <dl>
                  <div><dt>客户端</dt><dd>Tauri 2 + Next.js</dd></div>
                  <div><dt>Agent 服务</dt><dd>FastAPI + Pydantic AI</dd></div>
                  <div><dt>版本</dt><dd>0.1.0 · Prototype</dd></div>
                </dl>
              </section>
            ) : null}
          </div>

          <footer className="settings-footer">
            <div className="settings-message">
              {saveError ?? loadError ?? (!available ? "浏览器开发模式：设置为预览状态" : "")}
            </div>
            <button className="settings-cancel" type="button" onClick={onClose} disabled={saving}>取消</button>
            <button className="settings-save" type="submit" disabled={saving || !available}>
              {saving ? <LoaderCircle className="spinning" size={14} /> : <Check size={14} />}
              {saving ? "正在重启 Agent" : "保存并应用"}
            </button>
          </footer>
        </div>
      </form>
    </div>
  );
}

function SettingToggle({
  title,
  description,
  checked,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="setting-toggle-row">
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
      <button
        className={checked ? "toggle-switch is-on" : "toggle-switch"}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
    </div>
  );
}

function ChatWorkspace({
  conversationId,
  onNewConversation,
  workspace,
  workspaceLoading,
  onSelectWorkspace,
  model,
  workspaceWriteEnabled,
  onOpenReview,
}: {
  conversationId: string;
  onNewConversation: () => void;
  workspace: WorkspaceInfo | null;
  workspaceLoading: boolean;
  onSelectWorkspace: () => Promise<void>;
  model: string;
  workspaceWriteEnabled: boolean;
  onOpenReview: () => void;
}) {
  const [input, setInput] = useState("");
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE}/v1/chat/stream`,
        headers: {
          "X-Tenant-ID": TENANT_ID,
          ...(workspace ? { "X-Workspace-ID": workspace.id } : {}),
        },
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => ({
          body: {
            trigger,
            id,
            messages: trigger === "submit-message" ? messages.slice(-1) : messages,
            ...(messageId ? { messageId } : {}),
          },
        }),
      }),
    [workspace],
  );

  const {
    messages,
    sendMessage,
    status,
    error,
    setMessages,
    stop,
    addToolApprovalResponse,
  } = useChat({
    id: conversationId,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  });

  const isWorking = status === "submitted" || status === "streaming";

  useEffect(() => {
    const controller = new AbortController();

    async function initialize() {
      try {
        let online = false;
        for (let attempt = 0; attempt < 12 && !controller.signal.aborted; attempt += 1) {
          try {
            const health = await fetch(`${API_BASE}/health/ready`, {
              signal: controller.signal,
            });
            online = health.ok;
          } catch (healthError) {
            if (healthError instanceof DOMException && healthError.name === "AbortError") throw healthError;
          }
          if (online) break;
          await new Promise((resolve) => window.setTimeout(resolve, 350));
        }
        setApiOnline(online);

        if (!online) return;

        const history = await fetch(
          `${API_BASE}/v1/conversations/${conversationId}/messages`,
          {
            headers: { "X-Tenant-ID": TENANT_ID },
            signal: controller.signal,
          },
        );
        if (history.ok) {
          setMessages((await history.json()) as UIMessage[]);
        }
      } catch (fetchError) {
        if (!(fetchError instanceof DOMException && fetchError.name === "AbortError")) {
          setApiOnline(false);
        }
      } finally {
        if (!controller.signal.aborted) setHistoryLoaded(true);
      }
    }

    void initialize();
    return () => controller.abort();
  }, [conversationId, setMessages]);

  async function submitMessage(text: string) {
    const value = text.trim();
    if (!value || isWorking) return;
    setInput("");
    await sendMessage({ text: value });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  return (
    <>
      <header className="workspace-header">
        <div className="header-title">
          <span className="eyebrow">工作空间</span>
          <div className="title-row">
            <h1>智能助理</h1>
            <span className="model-pill" title={model}>{model.split(":").at(-1)}</span>
          </div>
        </div>
        <div className="header-actions">
          <button className="review-button" type="button" onClick={onOpenReview}>
            <FileDiff size={14} />
            变更
          </button>
          <button
            className={`workspace-button ${workspace ? "workspace-selected" : ""}`}
            type="button"
            onClick={() => void onSelectWorkspace()}
            disabled={workspaceLoading}
            title={workspace?.path}
          >
            <FolderOpen size={14} />
            {workspaceLoading ? "连接中" : workspace?.name ?? "打开仓库"}
          </button>
          <div className={`connection-status ${apiOnline ? "is-online" : "is-offline"}`}>
            <span />
            {apiOnline === null ? "检测中" : apiOnline ? "服务已连接" : "服务未连接"}
          </div>
          <button className="secondary-button" type="button" onClick={onNewConversation}>
            <MessageSquarePlus size={15} />
            新对话
          </button>
        </div>
      </header>

      <div className="conversation-stage">
        <div className="conversation-scroll">
          {!historyLoaded ? (
            <div className="history-loader">
              <span className="loading-dot" />
              正在恢复会话
            </div>
          ) : messages.length === 0 ? (
            <WelcomeState onSuggestion={submitMessage} />
          ) : (
            <div className="message-list">
              {messages
                .filter((message) => message.role !== "system")
                .map((message) => (
                  <MessageView
                    key={message.id}
                    message={message}
                    onToolApproval={(id, approved) =>
                      addToolApprovalResponse({
                        id,
                        approved,
                        reason: approved ? undefined : "User rejected this file write",
                      })
                    }
                  />
                ))}
              {status === "submitted" ? (
                <div className="assistant-thinking">
                  <div className="assistant-avatar">
                    <Image className="agent-image" src={agentBrand.iconSrc} alt="" width={31} height={31} />
                  </div>
                  <div className="thinking-bubble">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>

        <div className="composer-wrap">
          {error ? (
            <div className="error-banner">
              <span>模型请求失败。请检查后端是否运行，以及 `.env` 中的模型密钥。</span>
            </div>
          ) : null}
          <form ref={formRef} className="composer" onSubmit={handleSubmit}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="给 Agent 发消息…"
              rows={1}
              aria-label="消息"
            />
            <div className="composer-footer">
              <div className="composer-hint">
                <Sparkles size={13} />
                {workspace
                  ? `${workspaceWriteEnabled ? "受控读写" : "只读"}：${workspace.name}`
                  : workspaceWriteEnabled
                    ? "打开代码仓库后可读取源码，并在批准后写入文件"
                    : "打开代码仓库后可只读浏览与搜索源码"}
              </div>
              {isWorking ? (
                <button className="send-button stop-button" type="button" onClick={() => stop()}>
                  <Square size={13} fill="currentColor" />
                </button>
              ) : (
                <button className="send-button" type="submit" disabled={!input.trim()}>
                  <ArrowUp size={18} strokeWidth={2.4} />
                </button>
              )}
            </div>
          </form>
          <p className="composer-note">AI 可能会犯错，重要信息请自行核实。</p>
        </div>
      </div>
    </>
  );
}

function WelcomeState({ onSuggestion }: { onSuggestion: (message: string) => Promise<void> }) {
  return (
    <div className="welcome-state">
      <div className="welcome-orbit" aria-hidden="true">
        <div className="welcome-core">
          <Image className="agent-image" src={agentBrand.iconSrc} alt="" width={52} height={52} priority />
        </div>
        <span className="orbit-dot orbit-dot-one" />
        <span className="orbit-dot orbit-dot-two" />
      </div>
      <span className="welcome-kicker">YOUR LOCAL CODE AGENT</span>
      <h2>从一个代码仓库开始</h2>
      <p>选择本地项目后，我可以浏览与搜索源码；每次写入都会先展示内容并等待你的批准。</p>
      <div className="suggestion-grid">
        {suggestions.map(({ icon: Icon, label }) => (
          <button key={label} type="button" onClick={() => void onSuggestion(label)}>
            <Icon size={16} />
            <span>{label}</span>
            <ArrowUp className="suggestion-arrow" size={14} />
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageView({
  message,
  onToolApproval,
}: {
  message: UIMessage;
  onToolApproval: (id: string, approved: boolean) => void | PromiseLike<void>;
}) {
  const isUser = message.role === "user";
  return (
    <article className={`message-row ${isUser ? "message-user" : "message-assistant"}`}>
      {!isUser ? (
        <div className="assistant-avatar">
          <Image className="agent-image" src={agentBrand.iconSrc} alt="" width={31} height={31} />
        </div>
      ) : null}
      <div className="message-body">
        <div className="message-meta">
          <strong>{isUser ? "你" : agentBrand.name}</strong>
          {!isUser ? (
            <span className="verified-mark">
              <Check size={10} />
            </span>
          ) : null}
        </div>
        <div className="message-content">
          {message.parts.map((part, index) => (
            <MessagePartView
              key={`${message.id}-${index}`}
              part={part}
              onToolApproval={onToolApproval}
            />
          ))}
        </div>
      </div>
    </article>
  );
}

function MessagePartView({
  part,
  onToolApproval,
}: {
  part: UIMessage["parts"][number];
  onToolApproval: (id: string, approved: boolean) => void | PromiseLike<void>;
}) {
  if (part.type === "text") {
    return (
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
      </div>
    );
  }

  if (part.type === "reasoning") {
    return (
      <details className="reasoning-card">
        <summary>
          <Clock3 size={14} /> 查看思考过程
        </summary>
        <div className="reasoning-copy">{part.text}</div>
      </details>
    );
  }

  const genericPart = part as unknown as {
    type: string;
    toolName?: string;
    state?: string;
    input?: unknown;
    output?: unknown;
    approval?: {
      id: string;
      approved?: boolean;
      reason?: string;
    };
  };

  if (genericPart.type === "dynamic-tool" || genericPart.type.startsWith("tool-")) {
    const toolName = genericPart.toolName ?? genericPart.type.replace(/^tool-/, "");
    if (
      toolName === "write_file" &&
      genericPart.state === "approval-requested" &&
      genericPart.approval?.id
    ) {
      const input = (genericPart.input ?? {}) as {
        path?: unknown;
        content?: unknown;
        expected_sha256?: unknown;
      };
      const path = typeof input.path === "string" ? input.path : "unknown file";
      const content = typeof input.content === "string" ? input.content : "";
      const preview = content.length > 4000 ? `${content.slice(0, 4000)}\n...` : content;
      const operation = typeof input.expected_sha256 === "string" ? "覆盖文件" : "创建文件";

      return (
        <div className="write-approval-card">
          <div className="write-approval-heading">
            <div>
              <span>等待你的批准 · {operation}</span>
              <strong>{path}</strong>
            </div>
            <small>{new TextEncoder().encode(content).length} bytes</small>
          </div>
          <pre className="write-preview">
            <code>{preview || "(empty file)"}</code>
          </pre>
          <div className="write-approval-actions">
            <button
              className="write-reject-button"
              type="button"
              onClick={() => void onToolApproval(genericPart.approval!.id, false)}
            >
              拒绝
            </button>
            <button
              className="write-allow-button"
              type="button"
              onClick={() => void onToolApproval(genericPart.approval!.id, true)}
            >
              <Check size={13} /> 允许写入
            </button>
          </div>
        </div>
      );
    }

    const complete = genericPart.state === "output-available";
    const denied = genericPart.state === "output-denied";
    const failed = genericPart.state === "output-error";
    const terminal = complete || denied || failed;
    const stateLabel = complete
      ? "工具调用已完成"
      : denied
        ? "用户已拒绝"
        : failed
          ? "工具调用失败"
          : "正在调用工具";
    return (
      <div className="tool-card">
        <div className="tool-icon">
          <Wrench size={14} />
        </div>
        <div className="tool-copy">
          <strong>{formatToolName(toolName)}</strong>
          <span>{stateLabel}</span>
        </div>
        <span
          className={`tool-state ${complete ? "tool-complete" : ""} ${denied || failed ? "tool-denied" : ""}`}
        >
          {complete ? <Check size={12} /> : terminal ? "×" : <span className="loading-dot" />}
        </span>
      </div>
    );
  }

  return null;
}

function formatToolName(name: string) {
  if (name === "current_time") return "查询当前时间";
  if (name === "web_search") return "联网搜索";
  if (name === "list_files") return "浏览代码文件";
  if (name === "read_file") return "读取源文件";
  if (name === "search_text") return "搜索代码";
  if (name === "write_file") return "写入文件";
  return name.replaceAll("_", " ");
}
