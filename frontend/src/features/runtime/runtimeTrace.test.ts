import { describe, expect, it } from "vitest";

import {
  appendTraceStatus,
  compactToolSummary,
  hasPendingConfirmation,
  mergeToolResult,
  traceFromMessage
} from "./runtimeTrace";

describe("runtime trace helpers", () => {
  it("compacts legacy MCP summaries to their text content", () => {
    const summary = compactToolSummary(
      JSON.stringify({
        content: "Fetched page body",
        raw: { content: [{ type: "text", text: "Fetched page body" }], isError: false }
      })
    );

    expect(summary).toBe("Fetched page body");
  });

  it("deduplicates adjacent statuses and caps history", () => {
    expect(appendTraceStatus(["Planning"], "Planning")).toEqual(["Planning"]);
    expect(appendTraceStatus(["1", "2", "3", "4", "5", "6", "7", "8"], "9")).toEqual([
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9"
    ]);
  });

  it("restores a persisted confirmation block", () => {
    const trace = traceFromMessage({
      id: "assistant-1",
      role: "assistant",
      content: "",
      metadata: {
        tool_calls: [
          {
            tool_name: "read_file",
            arguments: { path: "README.md" },
            requires_confirmation: true,
            result: {
              status: "failed",
              error: "Tool requires confirmation before execution"
            }
          }
        ]
      }
    });

    expect(trace?.toolCalls[0].status).toBe("blocked");
    expect(hasPendingConfirmation(trace ?? undefined)).toBe(true);
  });

  it("merges a result into the latest matching tool call", () => {
    const calls = mergeToolResult(
      [
        {
          id: "call-1",
          tool_name: "search",
          status: "running",
          arguments: { query: "agent" }
        }
      ],
      {
        tool_name: "search",
        status: "success",
        duration_ms: 25,
        output_summary: "1 result",
        trace_id: "trace-1"
      }
    );

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ status: "success", output_summary: "1 result", trace_id: "trace-1" });
  });

  it.each(["cancelled", "approved", "completed", "interrupted", "expired"])(
    "never reopens a persisted %s approval", (status) => {
      const trace = traceFromMessage({
        id: "approval-1", role: "assistant", content: "",
        metadata: { tool_calls: [{
          tool_name: "read_file", arguments: { path: "README.md" },
          approval_id: "approval-1", approval_status: status, requires_confirmation: true,
          result: { status: "failed", error: "Tool requires confirmation before execution" }
        }] }
      });
      expect(hasPendingConfirmation(trace ?? undefined)).toBe(false);
      expect(trace?.toolCalls[0].id).toBe("approval-1");
      expect(trace?.toolCalls[0].requires_confirmation).toBe(false);
      expect(trace?.toolCalls[0].error).not.toBe("Tool requires confirmation before execution");
    }
  );

  it("restores the server approval ID and locally hides expired controls", () => {
    const message = {
      id: "approval-2", role: "assistant" as const, content: "",
      metadata: { tool_calls: [{
        tool_name: "fetch", arguments: { url: "https://example.com" },
        approval_id: "approval-2", approval_status: "pending", requires_confirmation: true,
        approval_expires_at: "2100-01-01T00:00:00Z",
        result: { status: "failed", error: "Tool requires confirmation before execution" }
      }] }
    };
    expect(traceFromMessage(message)?.toolCalls[0].approval_id).toBe("approval-2");
    expect(hasPendingConfirmation(traceFromMessage(message) ?? undefined)).toBe(true);
    message.metadata.tool_calls[0].approval_expires_at = "2000-01-01T00:00:00Z";
    expect(hasPendingConfirmation(traceFromMessage(message) ?? undefined)).toBe(false);
    expect(traceFromMessage(message)?.toolCalls[0].error).toContain("expired");
  });
});
