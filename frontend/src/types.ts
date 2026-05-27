export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type User = {
  id: string;
  username: string;
};

export type AuthResponse = {
  token: string;
  user: User;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type ToolManifest = {
  name: string;
  description: string;
  permission: string;
  enabled: boolean;
  parameters: Record<string, unknown>;
};

export type KnowledgeDocument = {
  id: string;
  conversation_id: string | null;
  title: string;
  source_type: string;
  status: string;
  created_at: string;
};

export type Citation = {
  document_id: string;
  title: string;
  chunk_index: number;
  content: string;
};

export type StreamEvent =
  | { event: "status"; data: { label: string; model?: string } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { conversation_id: string; citations: Citation[] } };
