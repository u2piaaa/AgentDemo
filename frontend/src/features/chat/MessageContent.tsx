import { isValidElement, memo, useDeferredValue, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function safeMessageUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function CopyButton({ text, label = "Copy answer" }: { text: string; label?: string }) {
  const [notice, setNotice] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Copied");
    } catch {
      setNotice("Copy unavailable. Select the text to copy.");
    }
  }
  return <span className="copy-control">
    <button className="icon-button copy-button" type="button" aria-label={label}
      title={label} onClick={() => void copy()}>
      {notice === "Copied" ? <Check size={16} /> : <Copy size={16} />}
    </button>
    <span className="copy-notice" role="status">{notice}</span>
  </span>;
}

export const MessageContent = memo(function MessageContent({ text }: { text: string }) {
  const renderedText = useDeferredValue(text);
  return <div className="markdown-body">
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeMessageUrl}
      components={{
        a: ({ href, children }) => href
          ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
          : <span>{children}</span>,
        // Remote images can carry tracking URLs. Show their descriptions without fetching them.
        img: ({ alt }) => <span className="image-description">{alt ? `[Image: ${alt}]` : "[Image]"}</span>,
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        table: ({ children }) => <div className="markdown-table" tabIndex={0} role="region"
          aria-label="Answer table"><table>{children}</table></div>,
      }}>
      {renderedText}
    </ReactMarkdown>
  </div>;
});

function CodeBlock({ children }: { children: ReactNode }) {
  const code = isValidElement<{ children?: ReactNode; className?: string }>(children) ? children : null;
  const text = typeof code?.props.children === "string" ? code.props.children : "";
  return <div className="code-block">
    <div className="code-toolbar"><span>{code?.props.className?.replace("language-", "") || "Code"}</span>
      <CopyButton text={text} label="Copy code" /></div>
    <pre tabIndex={0}>{children}</pre>
  </div>;
}
