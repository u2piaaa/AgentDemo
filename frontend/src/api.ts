import type {
  AuthResponse,
  Conversation,
  KnowledgeDocument,
  Message,
  StreamEvent,
  ToolManifest,
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
  if (!response.ok) throw new Error("Authentication required");
  return response.json();
}

export async function getConversations(): Promise<Conversation[]> {
  const response = await fetch("/api/conversations", { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to load conversations");
  return response.json();
}

export async function getMessages(conversationId: string): Promise<Message[]> {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, {
    headers: authHeaders()
  });
  if (!response.ok) throw new Error("Failed to load messages");
  return response.json();
}

export async function getTools(): Promise<ToolManifest[]> {
  const response = await fetch("/api/tools", { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to load tools");
  return response.json();
}

export async function getDocuments(conversationId: string | null): Promise<KnowledgeDocument[]> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`/api/knowledge/documents${query}`, { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to load documents");
  return response.json();
}

export async function createConversation(title = "New conversation"): Promise<Conversation> {
  const response = await fetch("/api/conversations", {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ title })
  });
  if (!response.ok) throw new Error("Failed to create conversation");
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
  if (!response.ok) throw new Error("Failed to update conversation title");
  return response.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: "DELETE",
    headers: authHeaders()
  });
  if (!response.ok) throw new Error("Failed to delete conversation");
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
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to upload document");
  }
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
  if (!response.ok || !response.body) throw new Error("Failed to start chat stream");

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

function parseSseChunk(chunk: string): StreamEvent[] {
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
  return [{ event: eventName, data: JSON.parse(dataLines.join("\n")) } as StreamEvent];
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json();
    return typeof payload.detail === "string" ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}
