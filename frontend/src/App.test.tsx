// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";
import * as api from "./api";
import type { StreamEvent } from "./types";

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  getAuthStatus: vi.fn().mockResolvedValue({ required: false }),
  getCurrentUser: vi.fn().mockResolvedValue({ id: "u1", username: "fixture" }),
  getConversations: vi.fn().mockResolvedValue([
    { id: "c1", title: "First chat" }, { id: "c2", title: "Second chat" },
  ]),
  getMessages: vi.fn(async (id) => [{ id: `a-${id}`, role: "assistant", content: id === "c1" ? "First saved answer" : "Second saved answer" }]),
  getDocuments: vi.fn().mockResolvedValue([]), getTools: vi.fn().mockResolvedValue([]),
  getMcpServers: vi.fn().mockResolvedValue([]), getMcpResources: vi.fn().mockResolvedValue([]),
  getMcpPrompts: vi.fn().mockResolvedValue([]), getTasks: vi.fn().mockResolvedValue([]),
  getTaskSchedules: vi.fn().mockResolvedValue([]), streamChat: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined), uploadDocument: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => cleanup());

async function openWorkspace() {
  render(<App />);
  await screen.findByText("First saved answer");
}

describe("workspace streaming lifecycle", () => {
  it("does not apply a late token or completion to a newly selected conversation", async () => {
    let receive!: (event: StreamEvent) => void;
    let finish!: () => void;
    let signal!: AbortSignal;
    vi.mocked(api.streamChat).mockImplementation((_text, _conversation, callback, incoming) => {
      receive = callback; signal = incoming!;
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    await openWorkspace();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Start streaming" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => receive({ event: "token", data: { text: "Old live answer" } }));
    fireEvent.click(screen.getByRole("button", { name: "Second chat" }));
    await screen.findByText("Second saved answer");
    expect(signal.aborted).toBe(true);
    await act(async () => {
      receive({ event: "token", data: { text: "Late leaked text" } });
      receive({ event: "done", data: { conversation_id: "c1", citations: [] } });
      finish();
    });
    expect(screen.queryByText(/Old live answer|Late leaked text/)).toBeNull();
    expect(screen.getByRole("heading", { name: "Second chat" })).toBeTruthy();
  });

  it("keeps a partial answer visible when the user stops generation", async () => {
    let receive!: (event: StreamEvent) => void;
    vi.mocked(api.streamChat).mockImplementation((_text, _conversation, callback, signal) => {
      receive = callback;
      return new Promise((_resolve, reject) => signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    });
    await openWorkspace();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Start streaming" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => receive({ event: "token", data: { text: "A useful partial answer" } }));
    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull());
    expect(screen.getByText("A useful partial answer")).toBeTruthy();
    expect(screen.getAllByText("Generation stopped").length).toBeGreaterThan(0);
  });

  it("keeps reading position while tokens arrive and provides a jump-to-latest control", async () => {
    let receive!: (event: StreamEvent) => void;
    vi.mocked(api.streamChat).mockImplementation((_text, _conversation, callback) => {
      receive = callback; return new Promise(() => {});
    });
    await openWorkspace();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Long answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const list = document.querySelector(".message-list")!;
    Object.defineProperties(list, { scrollHeight: { configurable: true, value: 2000 }, clientHeight: { configurable: true, value: 500 } });
    list.scrollTop = 100;
    fireEvent.scroll(list);
    await act(async () => receive({ event: "token", data: { text: "More content" } }));
    expect(list.scrollTop).toBe(100);
    fireEvent.click(screen.getByRole("button", { name: "Latest" }));
    expect(list.scrollTop).toBe(2000);
  });
});

it("opens a mobile inspector as a modal and closes it with Escape", async () => {
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  await openWorkspace();
  fireEvent.click(screen.getByRole("button", { name: "Open runtime details" }));
  const dialog = screen.getByRole("dialog", { name: "Runtime inspector" });
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(document.querySelector(".chat-panel")?.hasAttribute("inert")).toBe(true);
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Runtime inspector" })).toBeNull());
  expect(document.querySelector(".chat-panel")?.hasAttribute("inert")).toBe(false);
});
