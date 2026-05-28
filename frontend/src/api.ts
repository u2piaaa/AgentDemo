import type {
  AuthResponse,
  Conversation,
  KnowledgeDocument,
  Message,
  StreamEvent,
  Task,
  ToolCallData,
  ToolManifest,
  ToolResultData,
  User
} from "./types";

const jsonHeaders = { "Content-Type": "application/json" };
let authToken = localStorage.getItem("agent_auth_token") ?? "";

function authHeaders(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

function jsonAuthHeaders(): Record<string, string> {
  return { ...jsonHeaders, ...authHeaders() };
}

export function setAuthToken(token: string) {
  authToken = token;
  localStorage.setItem("agent_auth_token", token);
}

export function clearAuthToken() {
  authToken = "";
  localStorage.removeItem("agent_auth_token");
}

export async function getAuthStatus(): Promise<{ required: boolean }> {
  const response = await fetch("/api/auth/status");
  if (!response.ok) throw new Error("Failed to load auth status");
  return response.json();
}

export async function register(username: string, password: string): Promise<AuthResponse> {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) throw new Error(await readError(response, "Registration failed"));
  return response.json();
}

export async function login(username: string, password: string): Promise<AuthResponse> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) throw new Error(await readError(response, "Login failed"));
  return response.json();
}

export async function getCurrentUser(): Promise<User> {
  const response = await fetch("/api/auth/me", { headers: authHeaders() });
  if (!response.ok) throw new Error(await readError(response, "Authentication required"));
  return response.json();
}

export async function getConversations(): Promise<Conversation[]> {
  const response = await fetch("/api/conversations", { headers: authHeaders() });
  if (!response.ok) throw new Error(await readError(response, "Failed to load conversations"));
  return response.json();
}

export async function getMessages(conversationId: string): Promise<Message[]> {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, {
    headers: authHeaders()
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to load messages"));
  return response.json();
}

export async function getTools(): Promise<ToolManifest[]> {
  const response = await fetch("/api/tools", { headers: authHeaders() });
  if (!response.ok) throw new Error(await readError(response, "Failed to load tools"));
  return response.json();
}

export async function getDocuments(conversationId: string | null): Promise<KnowledgeDocument[]> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`/api/knowledge/documents${query}`, { headers: authHeaders() });
  if (!response.ok) throw new Error(await readError(response, "Failed to load documents"));
  return response.json();
}

export async function createConversation(title = "New conversation"): Promise<Conversation> {
  const response = await fetch("/api/conversations", {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ title })
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to create conversation"));
  return response.json();
}

export async function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<Conversation> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: "PATCH",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ title })
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to update conversation title"));
  return response.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: "DELETE",
    headers: authHeaders()
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to delete conversation"));
}

export async function uploadDocument(
  file: File,
  conversationId: string | null
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append("file", file);
  if (conversationId) {
    form.append("conversation_id", conversationId);
  }
  const response = await fetch("/api/knowledge/documents/upload", {
    method: "POST",
    headers: authHeaders(),
    body: form
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to upload document"));
  return response.json();
}

export async function getTasks(conversationId: string | null): Promise<Task[]> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`/api/tasks${query}`, { headers: authHeaders() });
  if (!response.ok) throw new Error(await readError(response, "Failed to load tasks"));
  return response.json();
}

export async function cancelTask(taskId: string): Promise<Task> {
  const response = await fetch(`/api/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: authHeaders()
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to cancel task"));
  return response.json();
}

export async function streamChat(
  message: string,
  conversationId: string | null,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const response = await fetch("/api/conversations/chat/stream", {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ message, conversation_id: conversationId })
  });
  if (!response.ok) throw new Error(await readError(response, "Failed to start chat stream"));
  if (!response.body) throw new Error("Failed to start chat stream: empty response body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const normalized = buffer.replace(/\r\n/g, "\n");
    const chunks = normalized.split("\n\n");
    buffer = done ? "" : chunks.pop() ?? "";

    for (const event of chunks.flatMap(parseSseChunk)) {
      onEvent(event);
    }
    if (done) break;
  }
}

export function parseSseChunk(chunk: string): StreamEvent[] {
  const lines = chunk.split("\n");
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (!dataLines.length) {
    return [];
  }
  try {
    return toStreamEvent(eventName, JSON.parse(dataLines.join("\n")));
  } catch {
    return [];
  }
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.clone().json();
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      const detail = payload.detail
        .map((item: unknown) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: unknown }).msg);
          }
          return "";
        })
        .filter(Boolean)
        .join("; ");
      return detail || fallback;
    }
    if (typeof payload.message === "string") return payload.message;
    if (typeof payload.error === "string") return payload.error;
    return fallback;
  } catch {
    try {
      const text = await response.text();
      return text || fallback;
    } catch {
      return fallback;
    }
  }
}

function toStreamEvent(eventName: string, data: unknown): StreamEvent[] {
  if (eventName === "status" && isRecord(data) && typeof data.label === "string") {
    return [{ event: "status", data: { label: data.label, model: stringOrUndefined(data.model) } }];
  }
  if (eventName === "token" && isRecord(data) && typeof data.text === "string") {
    return [{ event: "token", data: { text: data.text } }];
  }
  if (eventName === "plan" && isRecord(data)) {
    return [
      {
        event: "plan",
        data: {
          no_tool: Boolean(data.no_tool),
          tool_name: stringOrNull(data.tool_name),
          arguments: recordOrEmpty(data.arguments),
          reason: typeof data.reason === "string" ? data.reason : "",
          requires_confirmation: Boolean(data.requires_confirmation)
        }
      }
    ];
  }
  if (eventName === "tool_call" && isToolCallData(data)) {
    return [{ event: "tool_call", data }];
  }
  if (eventName === "tool_result" && isToolResultData(data)) {
    return [{ event: "tool_result", data }];
  }
  if (eventName === "error" && isRecord(data)) {
    return [
      {
        event: "error",
        data: {
          message: typeof data.message === "string" ? data.message : "Agent runtime error",
          trace_id: stringOrUndefined(data.trace_id)
        }
      }
    ];
  }
  if (eventName === "done" && isRecord(data) && typeof data.conversation_id === "string") {
    return [
      {
        event: "done",
        data: {
          conversation_id: data.conversation_id,
          citations: Array.isArray(data.citations) ? data.citations : [],
          tool_calls: Array.isArray(data.tool_calls) ? data.tool_calls : undefined,
          trace_id: stringOrUndefined(data.trace_id),
          model_route: isRecord(data.model_route) ? data.model_route : undefined
        }
      }
    ];
  }
  return [];
}

function isToolCallData(data: unknown): data is ToolCallData {
  return isRecord(data) && typeof data.tool_name === "string" && isRecord(data.arguments);
}

function isToolResultData(data: unknown): data is ToolResultData {
  return (
    isRecord(data) &&
    typeof data.tool_name === "string" &&
    typeof data.status === "string" &&
    typeof data.duration_ms === "number"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function recordOrEmpty(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}
