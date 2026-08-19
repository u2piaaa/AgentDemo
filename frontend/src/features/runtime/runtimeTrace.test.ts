import { describe, expect, it } from "vitest";

import {
  appendTraceStatus,
  hasPendingConfirmation,
  mergeToolResult,
  traceFromMessage
} from "./runtimeTrace";

describe("runtime trace helpers", () => {
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
});
