"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import Image from "next/image";
import {
  ArrowUp,
  Check,
  Clock3,
  Command,
  FileText,
  History,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  Search,
  Sparkles,
  Square,
  WandSparkles,
  Wrench,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentBrand } from "./brand";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const TENANT_ID = "local";
const CONVERSATION_STORAGE_KEY = "agent-product-conversation-id";

const suggestions = [
  { icon: Search, label: "帮我分析一个行业趋势" },
  { icon: FileText, label: "整理一份项目计划" },
  { icon: WandSparkles, label: "设计一个产品功能" },
];

function newConversationId() {
  return crypto.randomUUID();
}

export default function Home() {
  const [conversationId, setConversationId] = useState(() => {
    if (typeof window === "undefined") return "00000000-0000-4000-8000-000000000000";
    return window.localStorage.getItem(CONVERSATION_STORAGE_KEY) ?? newConversationId();
  });
  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, conversationId);
  }, [conversationId]);

  function startNewConversation() {
    const id = newConversationId();
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, id);
    setConversationId(id);
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
          <div className="profile-row">
            <div className="profile-avatar">游</div>
            <div className="profile-copy">
              <strong>本地用户</strong>
              <span>工作空间</span>
            </div>
            <Command size={15} />
          </div>
        </div>
      </aside>

      <section className="workspace">
        <ChatWorkspace
          key={conversationId}
          conversationId={conversationId}
          onNewConversation={startNewConversation}
        />
      </section>
    </main>
  );
}

function ChatWorkspace({
  conversationId,
  onNewConversation,
}: {
  conversationId: string;
  onNewConversation: () => void;
}) {
  const [input, setInput] = useState("");
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE}/v1/chat/stream`,
        headers: { "X-Tenant-ID": TENANT_ID },
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => ({
          body: {
            trigger,
            id,
            messages: trigger === "submit-message" ? messages.slice(-1) : messages,
            ...(messageId ? { messageId } : {}),
          },
        }),
      }),
    [],
  );

  const { messages, sendMessage, status, error, setMessages, stop } = useChat({
    id: conversationId,
    transport,
  });

  const isWorking = status === "submitted" || status === "streaming";

  useEffect(() => {
    const controller = new AbortController();

    async function initialize() {
      try {
        const health = await fetch(`${API_BASE}/health/ready`, { signal: controller.signal });
        setApiOnline(health.ok);

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
            <span className="model-pill">DeepSeek V4 Flash</span>
          </div>
        </div>
        <div className="header-actions">
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
                  <MessageView key={message.id} message={message} />
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
                支持工具调用与多轮上下文
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
      <span className="welcome-kicker">YOUR AI WORKSPACE</span>
      <h2>今天想一起完成什么？</h2>
      <p>我可以分析信息、制定计划，也可以调用工具执行具体任务。</p>
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

function MessageView({ message }: { message: UIMessage }) {
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
            <MessagePartView key={`${message.id}-${index}`} part={part} />
          ))}
        </div>
      </div>
    </article>
  );
}

function MessagePartView({ part }: { part: UIMessage["parts"][number] }) {
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
  };

  if (genericPart.type === "dynamic-tool" || genericPart.type.startsWith("tool-")) {
    const toolName = genericPart.toolName ?? genericPart.type.replace(/^tool-/, "");
    const complete = genericPart.state === "output-available";
    return (
      <div className="tool-card">
        <div className="tool-icon">
          <Wrench size={14} />
        </div>
        <div className="tool-copy">
          <strong>{formatToolName(toolName)}</strong>
          <span>{complete ? "工具调用已完成" : "正在调用工具"}</span>
        </div>
        <span className={`tool-state ${complete ? "tool-complete" : ""}`}>
          {complete ? <Check size={12} /> : <span className="loading-dot" />}
        </span>
      </div>
    );
  }

  return null;
}

function formatToolName(name: string) {
  if (name === "current_time") return "查询当前时间";
  if (name === "web_search") return "联网搜索";
  return name.replaceAll("_", " ");
}
