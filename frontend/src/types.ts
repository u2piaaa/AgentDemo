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
  metadata_?: Record<string, unknown>;
};

export type ToolManifest = {
  name: string;
  description: string;
  permission: string;
  requires_confirmation: boolean;
  enabled: boolean;
  parameters: Record<string, unknown>;
  timeout_seconds: number;
  output_strategy: Record<string, unknown>;
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

export type Task = {
  id: string;
  conversation_id: string | null;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "stale" | string;
  progress: number;
  error: string | null;
  result: Record<string, unknown> | null;
  trace_id: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
};

export type AgentPlanData = {
  no_tool: boolean;
  tool_name: string | null;
  arguments: Record<string, unknown>;
  reason: string;
  requires_confirmation: boolean;
};

export type ToolCallData = {
  tool_name: string;
  arguments: Record<string, unknown>;
  reason?: string;
  trace_id?: string;
  requires_confirmation?: boolean;
};

export type ToolResultData = {
  tool_name: string;
  status: string;
  output?: unknown;
  output_summary?: string | null;
  error?: string | null;
  duration_ms: number;
  trace_id?: string;
};

export type StreamEvent =
  | { event: "status"; data: { label: string; model?: string } }
  | { event: "token"; data: { text: string } }
  | { event: "plan"; data: AgentPlanData }
  | { event: "tool_call"; data: ToolCallData }
  | { event: "tool_result"; data: ToolResultData }
  | { event: "error"; data: { message: string; trace_id?: string } }
  | {
      event: "done";
      data: {
        conversation_id: string;
        citations: Citation[];
        tool_calls?: unknown[];
        trace_id?: string;
        model_route?: Record<string, unknown>;
      };
    };
