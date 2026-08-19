import type {
  AgentPlanData,
  Citation,
  Message,
  StreamEvent,
  ToolResultData
} from "../../types";

export type DraftMessage = Pick<Message, "role" | "content"> & {
  id: string;
  metadata?: Record<string, unknown>;
  metadata_?: Record<string, unknown>;
};

export type ToolStatus = "running" | "success" | "failed" | "timeout" | "blocked" | "cancelled";
export type ConfirmationDecision = "pending" | "confirmed" | "cancelled";

export type ToolTrace = {
  id: string;
  tool_name: string;
  provider?: string;
  provider_tool_id?: string | null;
  server_name?: string | null;
  status: ToolStatus;
  arguments: Record<string, unknown>;
  reason?: string;
  output_summary?: string | null;
  error?: string | null;
  duration_ms?: number;
  trace_id?: string;
  requires_confirmation?: boolean;
};

export type ExecutionTrace = {
  statuses?: string[];
  plans: AgentPlanData[];
  toolCalls: ToolTrace[];
  error?: string;
};

export function createExecutionTrace(): ExecutionTrace {
  return { statuses: [], plans: [], toolCalls: [] };
}

export function makeClientId(prefix: string) {
  try {
    const randomUUID = globalThis.crypto?.randomUUID;
    if (randomUUID) {
      return `${prefix}-${randomUUID.call(globalThis.crypto)}`;
    }
  } catch {
    // Non-local HTTP origins may not expose secure-context crypto APIs.
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function extractLatestCitations(messages: DraftMessage[]): Citation[] {
  for (const message of [...messages].reverse()) {
    if (message.role !== "assistant") continue;
    const citations = messageMetadata(message)?.citations;
    if (Array.isArray(citations)) return citations.filter(isCitation);
  }
  return [];
}

export function appendTraceStatus(statuses: string[] | undefined, label: string): string[] {
  const current = statuses ?? [];
  if (current[current.length - 1] === label) return current;
  return [...current, label].slice(-8);
}

export function runtimeStatusLabel(label: string, model?: string): string {
  const labels: Record<string, string> = {
    ensure_conversation: "Preparing conversation",
    load_history: "Loading conversation history",
    save_user_message: "Saving your message",
    retrieving_context: "Retrieving context - still working",
    planning: "Planning next step",
    generating: model ? `Generating answer with ${model}` : "Generating answer",
    save_assistant_message: "Saving answer",
    update_memory_summary: "Updating memory"
  };
  return labels[label] ?? (model ? `${label}: ${model}` : label);
}

export function compactSummary(value: unknown, maxLength = 180): string {
  const text = summarizeValue(value);
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

export function formatDuration(durationMs: number | undefined): string {
  if (typeof durationMs !== "number") return "n/a";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

export function traceFromMessage(message: DraftMessage): ExecutionTrace | null {
  const metadata = messageMetadata(message);
  const toolCalls = normalizePersistedToolCalls(metadata?.tool_calls);
  const plans = normalizePersistedPlans(metadata?.tool_calls);
  return toolCalls.length || plans.length ? { statuses: [], plans, toolCalls } : null;
}

export function hasVisibleTraceSteps(trace: ExecutionTrace | undefined): trace is ExecutionTrace {
  return Boolean(trace?.statuses?.length || trace?.plans.length || trace?.toolCalls.length || trace?.error);
}

export function hasPendingConfirmation(trace: ExecutionTrace | undefined): boolean {
  return Boolean(
    trace?.toolCalls.some(
      (tool) =>
        tool.requires_confirmation &&
        tool.status === "blocked" &&
        tool.error === "Tool requires confirmation before execution"
    )
  );
}

export function traceFromDoneEvent(
  data: Extract<StreamEvent, { event: "done" }>["data"]
): ExecutionTrace {
  const toolCalls = normalizePersistedToolCalls(data.tool_calls);
  const plans = normalizePersistedPlans(data.tool_calls);
  return {
    statuses: ["Done"],
    plans: plans.length
      ? plans
      : [
          {
            no_tool: true,
            tool_name: null,
            arguments: {},
            reason: "No tool call was used for this response.",
            requires_confirmation: false
          }
        ],
    toolCalls
  };
}

export function mergeToolResult(toolCalls: ToolTrace[], result: ToolResultData): ToolTrace[] {
  const targetIndex = [...toolCalls]
    .reverse()
    .findIndex((tool) => tool.tool_name === result.tool_name && ["running", "blocked"].includes(tool.status));
  const index = targetIndex === -1 ? -1 : toolCalls.length - 1 - targetIndex;
  const nextCall: ToolTrace = {
    id: result.trace_id ?? `${result.tool_name}-${Date.now().toString(36)}`,
    tool_name: result.tool_name,
    provider: result.provider,
    provider_tool_id: result.provider_tool_id,
    server_name: result.server_name,
    status: normalizeToolStatus(result.status),
    arguments: {},
    output_summary: result.output_summary,
    error: result.error,
    duration_ms: result.duration_ms,
    trace_id: result.trace_id
  };

  if (index === -1) return [...toolCalls, nextCall];
  const existing = toolCalls[index];
  const status =
    existing.requires_confirmation && result.error === "Tool requires confirmation before execution"
      ? "blocked"
      : normalizeToolStatus(result.status);
  return toolCalls.map((tool, currentIndex) =>
    currentIndex === index
      ? {
          ...tool,
          status,
          provider: result.provider ?? tool.provider,
          provider_tool_id: result.provider_tool_id ?? tool.provider_tool_id,
          server_name: result.server_name ?? tool.server_name,
          output_summary: result.output_summary,
          error: result.error,
          duration_ms: result.duration_ms,
          trace_id: result.trace_id ?? tool.trace_id
        }
      : tool
  );
}

function isCitation(value: unknown): value is Citation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Citation>;
  return (
    typeof candidate.document_id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.chunk_index === "number" &&
    typeof candidate.content === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringFromRecord(value: Record<string, unknown> | undefined, key: string): string | undefined {
  const item = value?.[key];
  return typeof item === "string" ? item : undefined;
}

function messageMetadata(message: DraftMessage): Record<string, unknown> | undefined {
  return message.metadata ?? message.metadata_;
}

function summarizeValue(value: unknown, fallback = "No data"): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value || fallback;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function normalizeToolStatus(status: unknown): ToolStatus {
  if (status === "success" || status === "timeout" || status === "cancelled") return status;
  if (status === "running" || status === "blocked") return status;
  return "failed";
}

function normalizePersistedToolCalls(raw: unknown): ToolTrace[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item, index) => {
    if (!isRecord(item) || typeof item.tool_name !== "string") return [];
    const result = isRecord(item.result) ? item.result : undefined;
    const status = normalizeToolStatus(result?.status);
    return [
      {
        id: `${item.tool_name}-${index}`,
        tool_name: item.tool_name,
        provider: typeof item.provider === "string" ? item.provider : stringFromRecord(result, "provider"),
        provider_tool_id:
          typeof item.provider_tool_id === "string"
            ? item.provider_tool_id
            : stringFromRecord(result, "provider_tool_id"),
        server_name:
          typeof item.server_name === "string" ? item.server_name : stringFromRecord(result, "server_name"),
        status: isConfirmationBlock(item, result) ? "blocked" : status,
        arguments: isRecord(item.arguments) ? item.arguments : {},
        reason: typeof item.reason === "string" ? item.reason : undefined,
        output_summary: typeof result?.output_summary === "string" ? result.output_summary : null,
        error: typeof result?.error === "string" ? result.error : null,
        duration_ms: typeof result?.duration_ms === "number" ? result.duration_ms : undefined,
        trace_id: typeof result?.trace_id === "string" ? result.trace_id : undefined,
        requires_confirmation: Boolean(item.requires_confirmation)
      }
    ];
  });
}

function normalizePersistedPlans(raw: unknown): AgentPlanData[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!isRecord(item) || typeof item.tool_name !== "string") return [];
    return [
      {
        no_tool: false,
        tool_name: item.tool_name,
        provider: typeof item.provider === "string" ? item.provider : undefined,
        provider_tool_id: typeof item.provider_tool_id === "string" ? item.provider_tool_id : null,
        server_name: typeof item.server_name === "string" ? item.server_name : null,
        arguments: isRecord(item.arguments) ? item.arguments : {},
        reason: typeof item.reason === "string" ? item.reason : `Use ${item.tool_name}`,
        requires_confirmation: Boolean(item.requires_confirmation)
      }
    ];
  });
}

function isConfirmationBlock(item: Record<string, unknown>, result: Record<string, unknown> | undefined) {
  return Boolean(item.requires_confirmation) && result?.error === "Tool requires confirmation before execution";
}
