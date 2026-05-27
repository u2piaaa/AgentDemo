import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Bot,
  Check,
  CircleDot,
  Edit2,
  FileUp,
  Hammer,
  Loader2,
  LogIn,
  LogOut,
  MessageSquarePlus,
  Send,
  Settings2,
  Trash2,
  UserPlus,
  X
} from "lucide-react";
import {
  clearAuthToken,
  createConversation,
  deleteConversation,
  getCurrentUser,
  getAuthStatus,
  getConversations,
  getDocuments,
  getMessages,
  getTools,
  login,
  register,
  setAuthToken,
  streamChat,
  updateConversationTitle,
  uploadDocument
} from "./api";
import type { Citation, Conversation, KnowledgeDocument, Message, ToolManifest, User } from "./types";

type DraftMessage = Pick<Message, "role" | "content"> & { id: string; metadata?: Record<string, unknown> };

function makeClientId(prefix: string) {
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

function extractLatestCitations(messages: DraftMessage[]): Citation[] {
  for (const message of [...messages].reverse()) {
    if (message.role !== "assistant") {
      continue;
    }
    const citations = message.metadata?.citations;
    if (Array.isArray(citations)) {
      return citations.filter(isCitation);
    }
  }
  return [];
}

function isCitation(value: unknown): value is Citation {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<Citation>;
  return (
    typeof candidate.document_id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.chunk_index === "number" &&
    typeof candidate.content === "string"
  );
}

export function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DraftMessage[]>([]);
  const [tools, setTools] = useState<ToolManifest[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("Idle");
  const [isStreaming, setIsStreaming] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [authError, setAuthError] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [uploadStatus, setUploadStatus] = useState("No document uploaded in this chat.");
  const [isUploading, setIsUploading] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [conversationToDelete, setConversationToDelete] = useState<Conversation | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getAuthStatus()
      .then(() => getCurrentUser())
      .then(setCurrentUser)
      .catch(() => {
        clearAuthToken();
        setCurrentUser(null);
      })
      .finally(() => setIsCheckingAuth(false));
  }, []);

  useEffect(() => {
    if (!currentUser) {
      return;
    }
    getConversations().then((items) => {
      setConversations(items);
      setActiveConversationId(items[0]?.id ?? null);
    });
    getTools().then(setTools);
  }, [currentUser]);

  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      setDocuments([]);
      setCitations([]);
      setUploadStatus("No document uploaded in this chat.");
      return;
    }
    if (isStreaming) {
      return;
    }
    Promise.all([getMessages(activeConversationId), getDocuments(activeConversationId)]).then(
      ([loadedMessages, loadedDocuments]) => {
        setMessages(loadedMessages);
        setDocuments(loadedDocuments);
        setUploadStatus(
          loadedDocuments.length
            ? `${loadedDocuments.length} document${loadedDocuments.length === 1 ? "" : "s"} in this chat.`
            : "No document uploaded in this chat."
        );
        setCitations(extractLatestCitations(loadedMessages));
      }
    );
  }, [activeConversationId, isStreaming]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;
    list.scrollTop = list.scrollHeight;
  }, [messages]);

  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === activeConversationId),
    [activeConversationId, conversations]
  );

  useEffect(() => {
    setIsEditingTitle(false);
    setTitleDraft(activeConversation?.title ?? "");
  }, [activeConversation?.id, activeConversation?.title]);

  async function handleNewConversation() {
    const conversation = await createConversation();
    setConversations((current) => [conversation, ...current]);
    setActiveConversationId(conversation.id);
    setMessages([]);
    setCitations([]);
    setDocuments([]);
    setUploadStatus("No document uploaded in this chat.");
  }

  async function handleAuthSubmit(event: FormEvent) {
    event.preventDefault();
    const username = authUsername.trim();
    if (!username || !authPassword) return;
    setAuthError("");

    try {
      const response =
        authMode === "login"
          ? await login(username, authPassword)
          : await register(username, authPassword);
      setAuthToken(response.token);
      setCurrentUser(response.user);
      setAuthPassword("");
    } catch (error) {
      clearAuthToken();
      setAuthError(error instanceof Error ? error.message : "Authentication failed");
    }
  }

  function handleLogout() {
    clearAuthToken();
    setCurrentUser(null);
    setConversations([]);
    setActiveConversationId(null);
    setMessages([]);
    setTools([]);
    setDocuments([]);
    setCitations([]);
    setStatus("Idle");
  }

  async function handleDeleteConversation() {
    if (!conversationToDelete) {
      return;
    }
    const deletedId = conversationToDelete.id;
    await deleteConversation(deletedId);
    setConversationToDelete(null);
    setConversations((current) => current.filter((conversation) => conversation.id !== deletedId));
    if (activeConversationId === deletedId) {
      const nextConversation = conversations.find((conversation) => conversation.id !== deletedId);
      setActiveConversationId(nextConversation?.id ?? null);
    }
  }

  async function handleSaveTitle(event: FormEvent) {
    event.preventDefault();
    if (!activeConversationId) return;
    const title = titleDraft.trim();
    if (!title) return;
    const updated = await updateConversationTitle(activeConversationId, title);
    setConversations((current) =>
      current.map((conversation) => (conversation.id === updated.id ? updated : conversation))
    );
    setIsEditingTitle(false);
  }

  function handleCancelTitleEdit() {
    setTitleDraft(activeConversation?.title ?? "");
    setIsEditingTitle(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || isStreaming) return;

    const assistantMessageId = makeClientId("assistant");
    setInput("");
    setIsStreaming(true);
    setStatus("Starting");
    setCitations([]);
    setMessages((current) => [
      ...current,
      { id: makeClientId("user"), role: "user", content: text },
      { id: assistantMessageId, role: "assistant", content: "" }
    ]);

    try {
      await streamChat(text, activeConversationId, (event) => {
        if (event.event === "status") {
          setStatus(event.data.model ? `${event.data.label}: ${event.data.model}` : event.data.label);
        }
        if (event.event === "token") {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantMessageId
                ? { ...message, content: message.content + event.data.text }
                : message
            )
          );
        }
        if (event.event === "done") {
          const conversationId = event.data.conversation_id;
          setActiveConversationId(conversationId);
          setCitations(event.data.citations);
          setStatus("Done");
          void getConversations().then(async (items) => {
            setConversations(items);
            const loadedMessages = await getMessages(conversationId);
            const loadedDocuments = await getDocuments(conversationId);
            setMessages(loadedMessages);
            setDocuments(loadedDocuments);
          });
        }
      });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Request failed");
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId && !message.content
            ? { ...message, content: "Request failed. Please try again." }
            : message
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }

  async function handleUploadDocument(file: File | undefined) {
    if (!file || isUploading) return;
    setIsUploading(true);
    setUploadStatus(`Uploading ${file.name}...`);
    try {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const conversation = await createConversation(`Document: ${file.name}`);
        conversationId = conversation.id;
        setConversations((current) => [conversation, ...current]);
        setActiveConversationId(conversation.id);
        setMessages([]);
        setCitations([]);
      }
      const document = await uploadDocument(file, conversationId);
      setDocuments((current) => [document, ...current]);
      setUploadStatus(`${document.title} is ready in this chat.`);
      setStatus("Document indexed");
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : "Document upload failed");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  if (isCheckingAuth) {
    return (
      <main className="auth-screen">
        <div className="auth-panel">
          <Bot size={32} aria-hidden="true" />
          <h1>Personal Agent</h1>
          <p className="muted">Checking session...</p>
        </div>
      </main>
    );
  }

  if (!currentUser) {
    return (
      <main className="auth-screen">
        <form className="auth-panel" onSubmit={handleAuthSubmit}>
          <Bot size={32} aria-hidden="true" />
          <h1>Personal Agent</h1>
          <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
            <button
              className={authMode === "login" ? "auth-tab active" : "auth-tab"}
              type="button"
              onClick={() => {
                setAuthMode("login");
                setAuthError("");
              }}
            >
              <LogIn size={16} aria-hidden="true" />
              Login
            </button>
            <button
              className={authMode === "register" ? "auth-tab active" : "auth-tab"}
              type="button"
              onClick={() => {
                setAuthMode("register");
                setAuthError("");
              }}
            >
              <UserPlus size={16} aria-hidden="true" />
              Register
            </button>
          </div>
          <label htmlFor="auth-username">Username</label>
          <input
            id="auth-username"
            type="text"
            value={authUsername}
            onChange={(event) => setAuthUsername(event.target.value)}
            autoComplete="username"
            minLength={3}
            maxLength={40}
          />
          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            type="password"
            value={authPassword}
            onChange={(event) => setAuthPassword(event.target.value)}
            autoComplete="current-password"
            minLength={4}
            maxLength={128}
          />
          {authError ? <p className="auth-error">{authError}</p> : null}
          <button className="primary-action">
            {authMode === "login" ? <LogIn size={18} aria-hidden="true" /> : <UserPlus size={18} aria-hidden="true" />}
            {authMode === "login" ? "Login" : "Create account"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Conversations">
        <div className="brand">
          <Bot size={24} aria-hidden="true" />
          <div>
            <h1>Personal Agent</h1>
            <p>{currentUser.username}</p>
          </div>
        </div>
        <button className="secondary-action" onClick={handleLogout}>
          <LogOut size={18} aria-hidden="true" />
          Logout
        </button>
        <button className="primary-action" onClick={handleNewConversation}>
          <MessageSquarePlus size={18} aria-hidden="true" />
          New chat
        </button>
        <nav className="conversation-list">
          {conversations.map((conversation) => (
            <div
              className={conversation.id === activeConversationId ? "conversation active" : "conversation"}
              key={conversation.id}
            >
              <button className="conversation-select" onClick={() => setActiveConversationId(conversation.id)}>
                <span>{conversation.title}</span>
              </button>
              <button
                className="conversation-delete"
                type="button"
                aria-label={`Delete ${conversation.title}`}
                title="Delete conversation"
                onClick={() => setConversationToDelete(conversation)}
                disabled={isStreaming}
              >
                <Trash2 size={16} aria-hidden="true" />
              </button>
            </div>
          ))}
        </nav>
      </aside>

      <section className="workspace" aria-label="Chat workspace">
        <header className="topbar">
          <div className="conversation-heading">
            <p className="eyebrow">Conversation</p>
            {isEditingTitle ? (
              <form className="title-editor" onSubmit={handleSaveTitle}>
                <input
                  aria-label="Conversation title"
                  value={titleDraft}
                  onChange={(event) => setTitleDraft(event.target.value)}
                  maxLength={200}
                  autoFocus
                />
                <button className="icon-button" aria-label="Save title" title="Save title">
                  <Check size={18} aria-hidden="true" />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Cancel title edit"
                  title="Cancel title edit"
                  onClick={handleCancelTitleEdit}
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </form>
            ) : (
              <div className="title-row">
                <h2>{activeConversation?.title ?? "New conversation"}</h2>
                {activeConversation ? (
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Edit title"
                    title="Edit title"
                    onClick={() => {
                      setTitleDraft(activeConversation.title);
                      setIsEditingTitle(true);
                    }}
                  >
                    <Edit2 size={18} aria-hidden="true" />
                  </button>
                ) : null}
              </div>
            )}
          </div>
          <div className="runtime-state" aria-live="polite">
            {isStreaming ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <CircleDot size={16} />}
            {status}
          </div>
        </header>

        <div className="content-grid">
          <section className="chat-panel" aria-label="Messages">
            <div className="message-list" ref={messageListRef}>
              {messages.length === 0 ? (
                <div className="empty-state">
                  <Bot size={32} aria-hidden="true" />
                  <h3>Start with a task, question, or document workflow.</h3>
                  <p>The first version is wired for streaming, local tool discovery, and RAG citations.</p>
                </div>
              ) : (
                messages.map((message) => (
                  <article className={`message ${message.role}`} key={message.id}>
                    <div className="message-role">{message.role}</div>
                    <p>{message.content}</p>
                  </article>
                ))
              )}
            </div>
            <form className="composer" onSubmit={handleSubmit}>
              <div className="upload-row">
                <input
                  ref={fileInputRef}
                  id="document-upload"
                  type="file"
                  accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
                  onChange={(event) => handleUploadDocument(event.target.files?.[0])}
                />
                <label className="upload-button" htmlFor="document-upload">
                  <FileUp size={18} aria-hidden="true" />
                  {isUploading ? "Indexing..." : "Upload document"}
                </label>
                <span aria-live="polite">{uploadStatus}</span>
              </div>
              <label htmlFor="chat-input">Message</label>
              <textarea
                id="chat-input"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ask the agent to plan, retrieve, call tools, or summarize."
                rows={3}
              />
              <button className="send-button" disabled={isStreaming || !input.trim()}>
                <Send size={18} aria-hidden="true" />
                Send
              </button>
            </form>
          </section>

          <aside className="inspector" aria-label="Runtime inspector">
            <section>
              <div className="section-title">
                <FileUp size={18} aria-hidden="true" />
                <h3>Documents</h3>
              </div>
              <div className="stack-list">
                {documents.length === 0 ? (
                  <p className="muted">Upload TXT, Markdown, or text PDF files for this chat.</p>
                ) : (
                  documents.map((document) => (
                    <div className="mini-card" key={document.id}>
                      <strong>{document.title}</strong>
                      <span>{document.source_type}</span>
                      <p>{document.status}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section>
              <div className="section-title">
                <Hammer size={18} aria-hidden="true" />
                <h3>Tools</h3>
              </div>
              <div className="stack-list">
                {tools.map((tool) => (
                  <div className="mini-card" key={tool.name}>
                    <strong>{tool.name}</strong>
                    <span>{tool.permission}</span>
                    <p>{tool.description}</p>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <div className="section-title">
                <BookOpen size={18} aria-hidden="true" />
                <h3>Citations</h3>
              </div>
              <div className="stack-list">
                {citations.length === 0 ? (
                  <p className="muted">No knowledge chunks used yet.</p>
                ) : (
                  citations.map((citation) => (
                    <div className="mini-card" key={`${citation.document_id}-${citation.chunk_index}`}>
                      <strong>{citation.title}</strong>
                      <span>Chunk {citation.chunk_index + 1}</span>
                      <p>{citation.content}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section>
              <div className="section-title">
                <Settings2 size={18} aria-hidden="true" />
                <h3>Runtime</h3>
              </div>
              <dl className="runtime-list">
                <div>
                  <dt>Agent graph</dt>
                  <dd>retrieve, plan, generate</dd>
                </div>
                <div>
                  <dt>Tasks</dt>
                  <dd>asyncio / APScheduler</dd>
                </div>
                <div>
                  <dt>Storage</dt>
                  <dd>PostgreSQL + pgvector</dd>
                </div>
              </dl>
            </section>
          </aside>
        </div>
      </section>
      {conversationToDelete ? (
        <div className="confirm-backdrop" role="presentation">
          <div
            className="confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-conversation-title"
          >
            <h3 id="delete-conversation-title">Delete conversation?</h3>
            <p>{conversationToDelete.title}</p>
            <div className="confirm-actions">
              <button className="icon-button text-button" onClick={() => setConversationToDelete(null)}>
                Cancel
              </button>
              <button className="danger-action" onClick={handleDeleteConversation}>
                <Trash2 size={16} aria-hidden="true" />
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
