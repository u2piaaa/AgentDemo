import { afterEach, describe, expect, it, vi } from "vitest";

import { cancelToolApproval, parseSseChunk, streamConfirmedTool } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("parseSseChunk", () => {
  it("parses a typed status event", () => {
    expect(
      parseSseChunk('event: status\ndata: {"label":"planning","model":"agent"}')
    ).toEqual([{ event: "status", data: { label: "planning", model: "agent" } }]);
  });

  it("normalizes done payload collections", () => {
    const [event] = parseSseChunk(
      'event: done\ndata: {"conversation_id":"c1","citations":null,"trace_id":"t1"}'
    );

    expect(event).toEqual({
      event: "done",
      data: {
        conversation_id: "c1",
        citations: [],
        trace_id: "t1"
      }
    });
  });

  it("drops malformed or unknown events", () => {
    expect(parseSseChunk("event: token\ndata: not-json")).toEqual([]);
    expect(parseSseChunk('event: internal\ndata: {"secret":true}')).toEqual([]);
  });
});

describe("durable approval requests", () => {
  it("sends the server approval ID with confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('event: done\ndata: {"conversation_id":"c1"}\n\n'));
    vi.stubGlobal("fetch", fetchMock);
    await streamConfirmedTool({
      conversationId: "c1", approvalId: "a1", message: "read page",
      toolName: "fetch", arguments: { url: "https://example.com" }
    }, () => {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      conversation_id: "c1", approval_id: "a1", tool_name: "fetch"
    });
  });

  it("persists cancellation and surfaces stale-approval errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"status":"cancelled"}'));
    vi.stubGlobal("fetch", fetchMock);
    await cancelToolApproval("c1", "a1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations/chat/confirm/cancel");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ conversation_id: "c1", approval_id: "a1" });
    fetchMock.mockResolvedValue(new Response('{"detail":"Approval already resolved"}', { status: 409 }));
    await expect(cancelToolApproval("c1", "a1")).rejects.toThrow("Approval already resolved");
  });
});
