// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CopyButton, MessageContent } from "./MessageContent";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("answer rendering", () => {
  it("renders headings, lists, code and GFM tables as semantic content", () => {
    const { container } = render(<MessageContent text={'## Summary\n\n- First point\n- Second point\n\n| Name | Value |\n| --- | --- |\n| A | 1 |\n\n```ts\nconst n = 1;\n```'} />);
    expect(screen.getByRole("heading", { name: "Summary" })).toBeTruthy();
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByRole("table").textContent).toContain("NameValueA1");
    expect(container.querySelector("pre")?.textContent).toContain("const n = 1;");
  });

  it("disallows active HTML, executable links and remote image requests", () => {
    const { container } = render(<MessageContent text={'<script>alert(1)</script>\n\n<img src="https://tracker.example/pixel">\n\n[unsafe](javascript:alert%281%29)\n\n[data](data:text/html,test)\n\n![pixel](https://tracker.example/pixel)\n\n[Source](https://example.com/source)'} />);
    expect(container.querySelector("script, img, iframe")).toBeNull();
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].href).toBe("https://example.com/source");
    expect(links[0].rel).toContain("noopener");
    expect(screen.getByText("[Image: pixel]")).toBeTruthy();
  });

  it("copies the original code, not its rendered toolbar", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<MessageContent text={'```js\nconsole.log("hello");\n```'} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('console.log("hello");\n'));
    expect(screen.getByRole("status").textContent).toBe("Copied");
  });

  it("provides a useful recovery message when clipboard permission is denied", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<CopyButton text="answer" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy answer" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Select the text"));
  });
});
