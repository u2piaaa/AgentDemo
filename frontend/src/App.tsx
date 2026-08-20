import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  Bot,
  Check,
  CheckCircle2,
  CircleDot,
  ClipboardList,
  Edit2,
  FileUp,
  Hammer,
  Loader2,
  LogIn,
  LogOut,
  Menu,
  MessageSquarePlus,
  PauseCircle,
  Send,
  Settings2,
  Trash2,
  UserPlus,
  X,
  XCircle
} from "lucide-react";
import {
  cancelTask,
  checkAccessToken,
  clearAccessToken,
  clearAuthToken,
  createAgentTask,
  createConversation,
  deleteConversation,
  getCurrentUser,
  getAuthStatus,
  getConversations,
  getDocuments,
  getMessages,
  getMcpPrompts,
  getMcpResources,
  getMcpServers,
  getTasks,
  getTools,
  login,
  register,
  setAccessToken,
  setAuthToken,
  streamChat,
  streamConfirmedTool,
  updateConversationTitle,
  uploadDocument
} from "./api";
import { TaskList } from "./features/tasks/TaskList";
import { isTaskTerminal, taskStatusLabel } from "./features/tasks/taskUtils";
import {
  appendTraceStatus,
  compactSummary,
  compactToolSummary,
  createExecutionTrace,
  extractLatestCitations,
  formatDuration,
  hasPendingConfirmation,
  hasVisibleTraceSteps,
  makeClientId,
  mergeToolResult,
  runtimeStatusLabel,
  traceFromDoneEvent,
  traceFromMessage
} from "./features/runtime/runtimeTrace";
import type {
  ConfirmationDecision,
  DraftMessage,
  ExecutionTrace,
  ToolTrace
} from "./features/runtime/runtimeTrace";
import type {
  Citation,
  Conversation,
  KnowledgeDocument,
  McpPrompt,
  McpResource,
  McpServer,
  StreamEvent,
  Task,
  ToolManifest,
  User
} from "./types";

function providerLabel(provider: string | undefined): string {
  return provider === "mcp_server" ? "MCP" : "Local";
}

function countLabel(count: number, label: string): string {
  return `${count} ${label}${count === 1 ? "" : "s"}`;
}

export function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DraftMessage[]>([]);
  const [tools, setTools] = useState<ToolManifest[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [mcpResources, setMcpResources] = useState<McpResource[]>([]);
  const [mcpPrompts, setMcpPrompts] = useState<McpPrompt[]>([]);
  const [mcpError, setMcpError] = useState("");
  const [isLoadingMcp, setIsLoadingMcp] = useState(true);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("Idle");
  const [isStreaming, setIsStreaming] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [accessRequired, setAccessRequired] = useState(false);
  const [hasAccess, setHasAccess] = useState(false);
  const [accessTokenInput, setAccessTokenInput] = useState("");
  const [accessError, setAccessError] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [authError, setAuthError] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskError, setTaskError] = useState("");
  const [isLoadingTasks, setIsLoadingTasks] = useState(false);
  const [isCreatingTask, setIsCreatingTask] = useState(false);
  const [cancellingTaskIds, setCancellingTaskIds] = useState<Set<string>>(() => new Set());
  const [uploadStatus, setUploadStatus] = useState("No document uploaded in this chat.");
  const [isUploading, setIsUploading] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [conversationToDelete, setConversationToDelete] = useState<Conversation | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(false);
  const [executionTraces, setExecutionTraces] = useState<Record<string, ExecutionTrace>>({});
  const [confirmationDecisions, setConfirmationDecisions] = useState<Record<string, ConfirmationDecision>>({});
  const executionTraceRef = useRef<Record<string, ExecutionTrace>>({});
  const pendingConfirmationConversationRef = useRef<string | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const taskStatusRef = useRef<Record<string, string>>({});

  useEffect(() => {
    getAuthStatus()
      .then(async (accessStatus) => {
        setAccessRequired(accessStatus.required);
        if (accessStatus.required) {
          const isAllowed = await checkAccessToken();
          if (!isAllowed) {
            clearAccessToken();
            clearAuthToken();
            setHasAccess(false);
            setCurrentUser(null);
            return;
          }
        }
        setHasAccess(true);
        try {
          setCurrentUser(await getCurrentUser());
        } catch {
          clearAuthToken();
          setCurrentUser(null);
        }
      })
      .catch(() => {
        clearAccessToken();
        clearAuthToken();
        setHasAccess(false);
        setCurrentUser(null);
      })
      .finally(() => setIsCheckingAuth(false));
  }, []);

  useEffect(() => {
    if (!currentUser) {
      setIsLoadingMcp(false);
      return;
    }
    getConversations().then((items) => {
      setConversations(items);
      setActiveConversationId(items[0]?.id ?? null);
    });
    getTools().then(setTools);
    setIsLoadingMcp(true);
    Promise.all([getMcpServers(), getMcpResources(), getMcpPrompts()])
      .then(([servers, resources, prompts]) => {
        setMcpServers(servers);
        setMcpResources(resources);
        setMcpPrompts(prompts);
        setMcpError("");
      })
      .catch((error) => setMcpError(error instanceof Error ? error.message : "Failed to load MCP"))
      .finally(() => setIsLoadingMcp(false));
  }, [currentUser]);

  useEffect(() => {
    if (isStreaming) {
      return;
    }
    if (!activeConversationId) {
      setMessages([]);
      setDocuments([]);
      setCitations([]);
      setTasks([]);
      executionTraceRef.current = {};
      setExecutionTraces({});
      setConfirmationDecisions({});
      setUploadStatus("No document uploaded in this chat.");
      return;
    }
    if (
      pendingConfirmationConversationRef.current === activeConversationId &&
      Object.values(executionTraceRef.current).some(hasPendingConfirmation)
    ) {
      return;
    }
    Promise.all([getMessages(activeConversationId), getDocuments(activeConversationId)]).then(
      ([loadedMessages, loadedDocuments]) => {
        setMessages(loadedMessages);
        setDocuments(loadedDocuments);
        const filteredTraces = (() => {
          const messageIds = new Set(loadedMessages.map((message) => message.id));
          return Object.fromEntries(
            Object.entries(executionTraceRef.current).filter(([messageId]) => messageIds.has(messageId))
          );
        })();
        executionTraceRef.current = filteredTraces;
        setExecutionTraces(filteredTraces);
        setConfirmationDecisions({});
        setUploadStatus(
          loadedDocuments.length
            ? `${loadedDocuments.length} document${loadedDocuments.length === 1 ? "" : "s"} in this chat.`
            : "No document uploaded in this chat."
        );
        setCitations(extractLatestCitations(loadedMessages));
      }
    );
  }, [activeConversationId, isStreaming]);

  function updateExecutionTrace(
    assistantMessageId: string,
    updater: (trace: ExecutionTrace) => ExecutionTrace
  ) {
    const nextTrace = updater(executionTraceRef.current[assistantMessageId] ?? createExecutionTrace());
    executionTraceRef.current = {
      ...executionTraceRef.current,
      [assistantMessageId]: nextTrace
    };
    setExecutionTraces((current) => ({
      ...current,
      [assistantMessageId]: nextTrace
    }));
  }

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;
    list.scrollTop = list.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!currentUser || !activeConversationId) {
      setTasks([]);
      setTaskError("");
      setIsLoadingTasks(false);
      return;
    }

    let isActive = true;
    async function loadConversationTasks(showLoading: boolean) {
      if (showLoading) {
        setIsLoadingTasks(true);
      }
      try {
        const loadedTasks = await getTasks(activeConversationId);
        if (!isActive) return;
        const completedTask = loadedTasks.find((task) => {
          const previousStatus = taskStatusRef.current[task.id];
          return (
            previousStatus !== undefined &&
            previousStatus !== task.status &&
            isTaskTerminal(task.status)
          );
        });
        taskStatusRef.current = Object.fromEntries(
          loadedTasks.map((task) => [task.id, task.status])
        );
        setTasks(loadedTasks);
        setTaskError("");
        if (completedTask && !isStreaming) {
          setStatus(taskStatusLabel(completedTask.status));
        }
      } catch (error) {
        if (!isActive) return;
        setTaskError(error instanceof Error ? error.message : "Failed to load tasks");
      } finally {
        if (isActive) {
          setIsLoadingTasks(false);
        }
      }
    }

    void loadConversationTasks(true);
    const interval = window.setInterval(() => void loadConversationTasks(false), 5000);
    return () => {
      isActive = false;
      window.clearInterval(interval);
    };
  }, [activeConversationId, currentUser, isStreaming]);

  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === activeConversationId),
    [activeConversationId, conversations]
  );

  const toolHistory = useMemo(() => {
    const fromMessages = messages.flatMap((message) => traceFromMessage(message)?.toolCalls ?? []);
    const fromLive = Object.values(executionTraces).flatMap((trace) => trace.toolCalls);
    const seen = new Set<string>();
    return [...fromMessages, ...fromLive].filter((tool) => {
      const key = `${tool.trace_id ?? tool.id}-${tool.tool_name}-${tool.duration_ms ?? ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [executionTraces, messages]);

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
    setTasks([]);
    executionTraceRef.current = {};
    setExecutionTraces({});
    setConfirmationDecisions({});
    setUploadStatus("No document uploaded in this chat.");
    setIsSidebarOpen(false);
  }

  function handleSelectConversation(conversationId: string) {
    setActiveConversationId(conversationId);
    setIsSidebarOpen(false);
  }

  async function handleAccessSubmit(event: FormEvent) {
    event.preventDefault();
    const token = accessTokenInput.trim();
    if (!token) return;
    setAccessError("");

    try {
      const isAllowed = await checkAccessToken(token);
      if (!isAllowed) {
        clearAccessToken();
        setAccessError("Invalid access token");
        return;
      }
      setAccessToken(token);
      setHasAccess(true);
      setAccessTokenInput("");
      try {
        setCurrentUser(await getCurrentUser());
      } catch {
        clearAuthToken();
        setCurrentUser(null);
      }
    } catch (error) {
      clearAccessToken();
      setAccessError(error instanceof Error ? error.message : "Invalid access token");
    }
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
    setTasks([]);
    setTaskError("");
    setCitations([]);
    executionTraceRef.current = {};
    setExecutionTraces({});
    setConfirmationDecisions({});
    setStatus("Idle");
    setIsSidebarOpen(false);
    setIsInspectorOpen(false);
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

  async function handleCancelTask(taskId: string) {
    if (cancellingTaskIds.has(taskId)) return;
    setCancellingTaskIds((current) => new Set(current).add(taskId));
    setTaskError("");
    try {
      const updated = await cancelTask(taskId);
      taskStatusRef.current = { ...taskStatusRef.current, [updated.id]: updated.status };
      setTasks((current) => current.map((task) => (task.id === updated.id ? updated : task)));
      if (!isStreaming) {
        setStatus(taskStatusLabel(updated.status));
      }
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : "Failed to cancel task");
    } finally {
      setCancellingTaskIds((current) => {
        const next = new Set(current);
        next.delete(taskId);
        return next;
      });
    }
  }

  async function handleCreateBackgroundTask() {
    const text = input.trim();
    if (!text || isStreaming || isCreatingTask) return;
    setIsCreatingTask(true);
    setTaskError("");
    setStatus("Queueing background task");
    try {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const conversation = await createConversation(text.slice(0, 80));
        conversationId = conversation.id;
        setConversations((current) => [conversation, ...current]);
        setActiveConversationId(conversation.id);
        setMessages([]);
        setDocuments([]);
        setCitations([]);
        executionTraceRef.current = {};
        setExecutionTraces({});
        setConfirmationDecisions({});
      }
      const task = await createAgentTask(text, conversationId);
      taskStatusRef.current = { ...taskStatusRef.current, [task.id]: task.status };
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setInput("");
      setStatus("Background task queued");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to create background task";
      setTaskError(message);
      setStatus(message);
    } finally {
      setIsCreatingTask(false);
    }
  }

  function handleCancelTool(toolId: string) {
    pendingConfirmationConversationRef.current = null;
    setConfirmationDecisions((current) => ({ ...current, [toolId]: "cancelled" }));
    setExecutionTraces((current) => {
      const next: Record<string, ExecutionTrace> = {};
      for (const [messageId, trace] of Object.entries(current)) {
        next[messageId] = {
          ...trace,
          toolCalls: trace.toolCalls.map((tool) =>
            tool.id === toolId ? { ...tool, status: "cancelled", error: "Cancelled in the frontend." } : tool
          )
        };
      }
      executionTraceRef.current = next;
      return next;
    });
  }

  function findPreviousUserMessage(assistantMessageId: string): string {
    const assistantIndex = messages.findIndex((message) => message.id === assistantMessageId);
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === "user") {
        return messages[index].content;
      }
    }
    return "";
  }

  async function handleConfirmTool(assistantSourceId: string, tool: ToolTrace) {
    if (!activeConversationId || isStreaming) return;
    const originalMessage = findPreviousUserMessage(assistantSourceId);
    if (!originalMessage) {
      setStatus("Cannot confirm tool call: original user message is unavailable.");
      return;
    }

    pendingConfirmationConversationRef.current = null;
    const assistantMessageId = makeClientId("assistant-confirmed");
    setConfirmationDecisions((current) => ({ ...current, [tool.id]: "confirmed" }));
    setIsStreaming(true);
    setStatus(`Continuing ${tool.tool_name}`);
    const initialTrace = createExecutionTrace();
    executionTraceRef.current = { ...executionTraceRef.current, [assistantMessageId]: initialTrace };
    setExecutionTraces((current) => ({ ...current, [assistantMessageId]: initialTrace }));
    setMessages((current) => [
      ...current,
      { id: assistantMessageId, role: "assistant", content: "" }
    ]);

    try {
      await streamConfirmedTool(
        {
          conversationId: activeConversationId,
          message: originalMessage,
          toolName: tool.tool_name,
          arguments: tool.arguments,
          reason: tool.reason ? `Confirmed: ${tool.reason}` : `Confirmed ${tool.tool_name}.`
        },
        (event) => handleRuntimeEvent(assistantMessageId, event)
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to continue tool call";
      setStatus(message);
      updateExecutionTrace(assistantMessageId, (trace) => ({ ...trace, error: message }));
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && !item.content
            ? { ...item, content: `Request failed: ${message}` }
            : item
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }

  function handleRuntimeEvent(assistantMessageId: string, event: StreamEvent) {
    if (event.event === "status") {
      const label = runtimeStatusLabel(event.data.label, event.data.model);
      setStatus(label);
      updateExecutionTrace(assistantMessageId, (trace) => ({
        ...trace,
        statuses: appendTraceStatus(trace.statuses, label)
      }));
    }
    if (event.event === "plan") {
      updateExecutionTrace(assistantMessageId, (trace) => ({
        ...trace,
        plans: [...trace.plans, event.data]
      }));
    }
    if (event.event === "tool_call") {
      updateExecutionTrace(assistantMessageId, (trace) => {
        const plan = [...trace.plans].reverse().find((item) => item.tool_name === event.data.tool_name);
        const toolId = `${event.data.trace_id ?? event.data.tool_name}-${trace.toolCalls.length}`;
        return {
          ...trace,
          toolCalls: [
            ...trace.toolCalls,
            {
              id: toolId,
              tool_name: event.data.tool_name,
              provider: event.data.provider,
              provider_tool_id: event.data.provider_tool_id,
              server_name: event.data.server_name,
              status: plan?.requires_confirmation ? "blocked" : "running",
              arguments: event.data.arguments,
              reason: event.data.reason ?? plan?.reason,
              trace_id: event.data.trace_id,
              requires_confirmation: event.data.requires_confirmation ?? plan?.requires_confirmation
            }
          ]
        };
      });
    }
    if (event.event === "tool_result") {
      updateExecutionTrace(assistantMessageId, (trace) => ({
        ...trace,
        toolCalls: mergeToolResult(trace.toolCalls, event.data)
      }));
    }
    if (event.event === "error") {
      setStatus(event.data.message);
      updateExecutionTrace(assistantMessageId, (trace) => ({ ...trace, error: event.data.message }));
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
      const liveTrace = executionTraceRef.current[assistantMessageId];
      const awaitingConfirmation = hasPendingConfirmation(liveTrace);
      setActiveConversationId(conversationId);
      setCitations(event.data.citations);
      setStatus(awaitingConfirmation ? "Waiting for tool confirmation" : "Done");
      pendingConfirmationConversationRef.current = awaitingConfirmation ? conversationId : null;
      void getConversations().then(async (items) => {
        setConversations(items);
        const loadedDocuments = await getDocuments(conversationId);
        if (awaitingConfirmation) {
          setDocuments(loadedDocuments);
          void getTasks(conversationId).then(setTasks).catch(() => undefined);
          return;
        }
        const loadedMessages = await getMessages(conversationId);
        const persistedAssistant = [...loadedMessages].reverse().find((message) => message.role === "assistant");
        if (persistedAssistant) {
          const finalTrace = hasVisibleTraceSteps(liveTrace) ? liveTrace : traceFromDoneEvent(event.data);
          executionTraceRef.current = {
            ...executionTraceRef.current,
            [persistedAssistant.id]: finalTrace
          };
          setExecutionTraces((current) => ({ ...current, [persistedAssistant.id]: finalTrace }));
        }
        setMessages(loadedMessages);
        setDocuments(loadedDocuments);
        void getTasks(conversationId).then(setTasks).catch(() => undefined);
      });
    }
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
    const initialTrace = createExecutionTrace();
    executionTraceRef.current = { ...executionTraceRef.current, [assistantMessageId]: initialTrace };
    setExecutionTraces((current) => ({ ...current, [assistantMessageId]: initialTrace }));
    setMessages((current) => [
      ...current,
      { id: makeClientId("user"), role: "user", content: text },
      { id: assistantMessageId, role: "assistant", content: "" }
    ]);

    try {
      await streamChat(text, activeConversationId, (event) => handleRuntimeEvent(assistantMessageId, event));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Request failed";
      setStatus(message);
      updateExecutionTrace(assistantMessageId, (trace) => ({ ...trace, error: message }));
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && !item.content
            ? { ...item, content: `Request failed: ${message}` }
            : item
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.ctrlKey ||
      event.altKey ||
      event.metaKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }
    event.preventDefault();
    if (!isStreaming && input.trim()) {
      event.currentTarget.form?.requestSubmit();
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
        setTasks([]);
        executionTraceRef.current = {};
        setExecutionTraces({});
        setConfirmationDecisions({});
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

  function renderExecutionTrace(message: DraftMessage) {
    const trace = executionTraces[message.id] ?? traceFromMessage(message);
    if (!hasVisibleTraceSteps(trace ?? undefined)) return null;

    return (
      <div className="execution-trace" aria-label="Agent execution steps">
        {trace?.statuses?.map((label, index) => (
          <div className="execution-step status-step" key={`${label}-${index}`}>
            <CircleDot size={15} aria-hidden="true" />
            <div>
              <strong>Status</strong>
              <p>{label}</p>
            </div>
          </div>
        ))}
        {trace?.plans.map((plan, index) => (
          <div className="execution-step plan-step" key={`${plan.tool_name ?? "plan"}-${index}`}>
            <ClipboardList size={15} aria-hidden="true" />
            <div>
              <strong>Plan</strong>
              <p>{plan.reason || `Use ${plan.tool_name}`}</p>
              {plan.tool_name ? <code>{plan.tool_name}</code> : <code>no tool</code>}
              {plan.provider ? <span className="provider-pill">{providerLabel(plan.provider)}</span> : null}
              {plan.server_name ? <span className="muted">Server {plan.server_name}</span> : null}
            </div>
          </div>
        ))}
        {trace?.toolCalls.map((tool) => renderToolStep(message.id, tool))}
        {trace?.error ? (
          <div className="execution-step failed">
            <AlertCircle size={15} aria-hidden="true" />
            <div>
              <strong>Error</strong>
              <p>{trace.error}</p>
            </div>
          </div>
        ) : null}
        {message.content ? (
          <div className="execution-step final-step">
            <CheckCircle2 size={15} aria-hidden="true" />
            <div>
              <strong>Final answer</strong>
              <p>Streaming response delivered below.</p>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  function renderToolStep(messageId: string, tool: ToolTrace) {
    const decision = confirmationDecisions[tool.id];
    const needsConfirmation =
      tool.requires_confirmation && tool.status === "blocked" && !["cancelled", "confirmed"].includes(decision ?? "");
    return (
      <div className={`execution-step tool-step ${tool.status}`} key={tool.id}>
        {tool.status === "running" ? (
          <Loader2 className="spin" size={15} aria-hidden="true" />
        ) : tool.status === "success" ? (
          <CheckCircle2 size={15} aria-hidden="true" />
        ) : tool.status === "cancelled" ? (
          <PauseCircle size={15} aria-hidden="true" />
        ) : (
          <XCircle size={15} aria-hidden="true" />
        )}
        <div>
          <div className="step-heading">
            <strong>{tool.tool_name}</strong>
            <span>{tool.status === "running" ? "running" : `${tool.status} · ${formatDuration(tool.duration_ms)}`}</span>
          </div>
          <div className="provider-row">
            <span className="provider-pill">{providerLabel(tool.provider)}</span>
            {tool.server_name ? <span>{tool.server_name}</span> : null}
            {tool.provider_tool_id ? <code>{tool.provider_tool_id}</code> : null}
          </div>
          {tool.reason ? <p>{tool.reason}</p> : null}
          <pre>{compactSummary(tool.arguments)}</pre>
          {tool.output_summary ? <p>{compactToolSummary(tool.output_summary)}</p> : null}
          {tool.error ? <p className="inline-error">{tool.error}</p> : null}
          {needsConfirmation ? (
            <div className="confirmation-box">
              <p>This tool requires confirmation before it can run.</p>
              <div className="confirmation-actions">
                <button
                  className="secondary-action compact"
                  type="button"
                  disabled={isStreaming}
                  onClick={() => void handleConfirmTool(messageId, tool)}
                >
                  <Check size={15} aria-hidden="true" />
                  Confirm
                </button>
                <button className="danger-action compact" type="button" onClick={() => handleCancelTool(tool.id)}>
                  <X size={15} aria-hidden="true" />
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    );
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

  if (accessRequired && !hasAccess) {
    return (
      <main className="auth-screen">
        <form className="auth-panel" onSubmit={handleAccessSubmit}>
          <Bot size={32} aria-hidden="true" />
          <h1>Personal Agent</h1>
          <p className="muted">Enter the project access token.</p>
          <label htmlFor="access-token">Access token</label>
          <input
            id="access-token"
            type="password"
            value={accessTokenInput}
            onChange={(event) => setAccessTokenInput(event.target.value)}
            autoComplete="current-password"
          />
          {accessError ? <p className="auth-error">{accessError}</p> : null}
          <button className="primary-action">
            <LogIn size={18} aria-hidden="true" />
            Continue
          </button>
        </form>
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
      <aside className={isSidebarOpen ? "sidebar mobile-open" : "sidebar"} aria-label="Conversations">
        <div className="brand">
          <Bot size={24} aria-hidden="true" />
          <div>
            <h1>Personal Agent</h1>
            <p>{currentUser.username}</p>
          </div>
          <button
            className="icon-button sidebar-close"
            type="button"
            aria-label="Close conversations"
            title="Close conversations"
            onClick={() => setIsSidebarOpen(false)}
          >
            <X size={18} aria-hidden="true" />
          </button>
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
              <button className="conversation-select" onClick={() => handleSelectConversation(conversation.id)}>
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
          <button
            className="icon-button mobile-menu-button"
            type="button"
            aria-label="Open conversations"
            title="Open conversations"
            onClick={() => {
              setIsInspectorOpen(false);
              setIsSidebarOpen(true);
            }}
          >
            <Menu size={20} aria-hidden="true" />
          </button>
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
          <div className="topbar-actions">
            <button
              className="icon-button mobile-inspector-button"
              type="button"
              aria-label="Open runtime details"
              title="Open runtime details"
              onClick={() => {
                setIsSidebarOpen(false);
                setIsInspectorOpen(true);
              }}
            >
              <Settings2 size={18} aria-hidden="true" />
            </button>
            <div className="runtime-state" aria-live="polite">
              {isStreaming ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <CircleDot size={16} />}
              {status}
            </div>
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
                    {message.role === "assistant" ? renderExecutionTrace(message) : null}
                    {message.content ? <p>{message.content}</p> : null}
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
                onKeyDown={handleComposerKeyDown}
                placeholder="Ask the agent to plan, retrieve, call tools, or summarize."
                rows={3}
              />
              <div className="composer-actions">
                <button
                  className="background-task-button"
                  type="button"
                  disabled={isStreaming || isCreatingTask || !input.trim()}
                  onClick={() => void handleCreateBackgroundTask()}
                >
                  {isCreatingTask ? (
                    <Loader2 className="spin" size={18} aria-hidden="true" />
                  ) : (
                    <ClipboardList size={18} aria-hidden="true" />
                  )}
                  {isCreatingTask ? "Queueing" : "Run in background"}
                </button>
                <button className="send-button" disabled={isStreaming || isCreatingTask || !input.trim()}>
                  <Send size={18} aria-hidden="true" />
                  Send
                </button>
              </div>
            </form>
          </section>

          <aside className={isInspectorOpen ? "inspector mobile-open" : "inspector"} aria-label="Runtime inspector">
            <div className="inspector-mobile-header">
              <h2>Runtime details</h2>
              <button
                className="icon-button"
                type="button"
                aria-label="Close runtime details"
                title="Close runtime details"
                onClick={() => setIsInspectorOpen(false)}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <section>
              <div className="section-title">
                <ClipboardList size={18} aria-hidden="true" />
                <h3>Tasks</h3>
              </div>
              <TaskList
                tasks={tasks}
                error={taskError}
                isLoading={isLoadingTasks}
                cancellingTaskIds={cancellingTaskIds}
                onCancel={(taskId) => void handleCancelTask(taskId)}
              />
            </section>
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
                <Settings2 size={18} aria-hidden="true" />
                <h3>MCP</h3>
              </div>
              <div className="stack-list">
                {mcpError ? <p className="inline-error">{mcpError}</p> : null}
                {isLoadingMcp ? (
                  <p className="muted">Loading MCP servers...</p>
                ) : mcpServers.length === 0 ? (
                  <p className="muted">No MCP servers configured.</p>
                ) : (
                  mcpServers.map((server) => {
                    const availableToolCount = tools.filter(
                      (tool) => tool.server_name === server.name
                    ).length;
                    const resourceCount = mcpResources.filter(
                      (resource) => resource.server_name === server.name
                    ).length;
                    const promptCount = mcpPrompts.filter(
                      (prompt) => prompt.server_name === server.name
                    ).length;
                    return (
                      <div className="mini-card" key={server.name}>
                        <div className="history-card-title">
                          <strong>{server.name}</strong>
                          <span className={`status-pill ${server.status}`}>{server.status}</span>
                        </div>
                        <span>{server.transport}</span>
                        <p>
                          {countLabel(availableToolCount, "available tool")} · {countLabel(resourceCount, "resource")} · {countLabel(promptCount, "prompt")}
                        </p>
                        {server.error ? <p className="inline-error">{server.error}</p> : null}
                      </div>
                    );
                  })
                )}
                {mcpResources.slice(0, 4).map((resource) => (
                  <div className="mini-card" key={`${resource.server_name}-${resource.uri}`}>
                    <strong>{resource.name || resource.uri}</strong>
                    <span>{resource.server_name}</span>
                    <code>{resource.uri}</code>
                  </div>
                ))}
                {mcpPrompts.slice(0, 4).map((prompt) => (
                  <div className="mini-card" key={`${prompt.server_name}-${prompt.name}`}>
                    <strong>{prompt.name}</strong>
                    <span>{prompt.server_name}</span>
                    {prompt.description ? <p>{prompt.description}</p> : null}
                  </div>
                ))}
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
                    <div className="history-card-title">
                      <strong>{tool.name}</strong>
                      <span className="provider-pill">{providerLabel(tool.provider)}</span>
                    </div>
                    <span>{tool.requires_confirmation ? `${tool.permission} · confirm` : tool.permission}</span>
                    <p>{tool.description}</p>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <div className="section-title">
                <Settings2 size={18} aria-hidden="true" />
                <h3>Tool history</h3>
              </div>
              <div className="stack-list">
                {toolHistory.length === 0 ? (
                  <p className="muted">No tool calls in this conversation yet.</p>
                ) : (
                  toolHistory.map((tool, index) => (
                    <div className="mini-card tool-history-card" key={`${tool.id}-${index}`}>
                      <div className="history-card-title">
                        <strong>{tool.tool_name}</strong>
                        <span className={`status-pill ${tool.status}`}>{tool.status}</span>
                      </div>
                      <div className="provider-row">
                        <span className="provider-pill">{providerLabel(tool.provider)}</span>
                        {tool.server_name ? <span>{tool.server_name}</span> : null}
                      </div>
                      <span>{formatDuration(tool.duration_ms)}</span>
                      <pre>{compactSummary(tool.arguments)}</pre>
                      {tool.output_summary ? <p>{compactToolSummary(tool.output_summary)}</p> : null}
                      {tool.error ? <p className="inline-error">{tool.error}</p> : null}
                    </div>
                  ))
                )}
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
                  <dd>durable asyncio workers</dd>
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
      {isSidebarOpen || isInspectorOpen ? (
        <button
          className="mobile-scrim"
          type="button"
          aria-label="Close mobile panel"
          onClick={() => {
            setIsSidebarOpen(false);
            setIsInspectorOpen(false);
          }}
        />
      ) : null}
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
