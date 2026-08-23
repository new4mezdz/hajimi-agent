const IPC_ORIGIN = "ipc://agent";
const BROWSER_API_ORIGIN =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export const AGENT_API_BASE = IPC_ORIGIN;

type AgentIpcEvent = {
  type: "response_start" | "response_chunk" | "response_end" | "error" | "cancelled";
  status?: number;
  headers?: [string, string][];
  data?: string;
  message?: string;
};

type AgentIpcRequest = {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: string | null;
};

function inputUrl(input: RequestInfo | URL) {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function ipcPath(input: RequestInfo | URL) {
  const url = inputUrl(input);
  if (url.startsWith(IPC_ORIGIN)) {
    const path = url.slice(IPC_ORIGIN.length);
    return path || "/";
  }
  if (url.startsWith("/")) return url;
  throw new Error(`Agent IPC only accepts local application paths: ${url}`);
}

function browserUrl(input: RequestInfo | URL) {
  const url = inputUrl(input);
  if (url.startsWith(IPC_ORIGIN)) return `${BROWSER_API_ORIGIN}${ipcPath(input)}`;
  if (url.startsWith("/")) return `${BROWSER_API_ORIGIN}${url}`;
  return url;
}

function decodeBase64(value: string) {
  const binary = window.atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function requestBody(input: RequestInfo | URL, init?: RequestInit) {
  if (init?.body !== undefined && init.body !== null) {
    if (typeof init.body !== "string") {
      throw new Error("Agent IPC currently accepts UTF-8 string request bodies only");
    }
    return init.body;
  }
  if (input instanceof Request && input.body !== null) {
    return input.clone().text();
  }
  return null;
}

async function desktopFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const { Channel, invoke } = await import("@tauri-apps/api/core");
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((value, name) => headers.set(name, value));
  const serializedHeaders: Record<string, string> = {};
  headers.forEach((value, name) => {
    serializedHeaders[name] = value;
  });
  const request: AgentIpcRequest = {
    method: (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase(),
    path: ipcPath(input),
    headers: serializedHeaders,
    body: await requestBody(input, init),
  };

  const signal = init?.signal;
  const onEvent = new Channel<AgentIpcEvent>();
  let requestId: string | null = null;
  let responseStarted = false;
  let finished = false;
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
  let rejectResponse: ((reason?: unknown) => void) | null = null;

  const cancelBackend = () => {
    if (requestId) {
      void invoke("agent_cancel", { requestId }).catch(() => undefined);
    }
  };
  const abortError = () => new DOMException("The Agent request was aborted", "AbortError");
  const cleanup = () => {
    signal?.removeEventListener("abort", handleAbort);
    onEvent.onmessage = () => undefined;
  };
  const fail = (error: unknown) => {
    if (finished) return;
    finished = true;
    cleanup();
    if (responseStarted) streamController?.error(error);
    else rejectResponse?.(error);
  };
  const handleAbort = () => {
    cancelBackend();
    fail(abortError());
  };

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
    },
    cancel() {
      cancelBackend();
      if (!finished) {
        finished = true;
        cleanup();
      }
    },
  });

  const response = new Promise<Response>((resolve, reject) => {
    rejectResponse = reject;
    onEvent.onmessage = (event) => {
      if (finished) return;
      if (event.type === "response_start") {
        if (responseStarted || event.status === undefined) {
          fail(new Error("The Agent engine returned an invalid response start event"));
          return;
        }
        responseStarted = true;
        resolve(
          new Response(stream, {
            status: event.status,
            headers: event.headers,
          }),
        );
        return;
      }
      if (event.type === "response_chunk") {
        if (!responseStarted || event.data === undefined) {
          fail(new Error("The Agent engine returned an invalid response chunk"));
          return;
        }
        streamController?.enqueue(decodeBase64(event.data));
        return;
      }
      if (event.type === "response_end") {
        if (!responseStarted) {
          fail(new Error("The Agent engine ended before starting a response"));
          return;
        }
        finished = true;
        streamController?.close();
        cleanup();
        return;
      }
      fail(
        event.type === "cancelled"
          ? abortError()
          : new Error(event.message ?? "The local Agent engine failed"),
      );
    };
  });

  signal?.addEventListener("abort", handleAbort, { once: true });
  if (signal?.aborted) handleAbort();

  try {
    requestId = await invoke<string>("agent_request", { request, onEvent });
    if (signal?.aborted) cancelBackend();
  } catch (error) {
    fail(error);
  }
  return response;
}

export const agentFetch: typeof globalThis.fetch = async (input, init) => {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    const target = browserUrl(input);
    if (input instanceof Request) {
      return globalThis.fetch(new Request(target, input), init);
    }
    return globalThis.fetch(target, init);
  }
  return desktopFetch(input, init);
};
