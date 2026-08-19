import { describe, expect, it } from "vitest";

import { parseSseChunk } from "./api";

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
