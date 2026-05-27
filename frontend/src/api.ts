import type { Conversation, KnowledgeDocument, Message, StreamEvent, ToolManifest } from "./types";

const jsonHeaders = { "Content-Type": "application/json" };
let accessToken = sessionStorage.getItem("agent_access_token") ?? "";

function authHeaders(): Record<string, string> {
  return accessToken ? { "x-agent-access-token": accessToken } : {};
}

function jsonAuthHeaders(): Record<string, string> {
  return { ...jsonHeaders, ...authHeaders() };
}

export function setAccessToken(token: string) {
  accessToken = token;
  sessionStorage.setItem("agent_access_token", token);
}

export function clearAccessToken() {
  accessToken = "";
  sessionStorage.removeItem("agent_access_token");
}

export async function getAuthStatus(): Promise<{ required: boolean }> {
  const response = await fetch("/api/auth/status");
  if (!response.ok) throw new Error("Failed to load auth status");
  return response.json();
}

export async function checkAccessToken(token: string): Promise<boolean> {
  const response = await fetch("/api/auth/check", {
    method: "POST",
    headers: { "x-agent-access-token": token }
  });
  if (!response.ok) return false;
  return (await response.json()).ok === true;
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
