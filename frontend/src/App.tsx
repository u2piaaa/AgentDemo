import { FormEvent, KeyboardEvent, useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Asterisk,
  ArrowDown,
  ArrowUp,
  AlertCircle,
  BookOpen,
  Bot,
  CalendarClock,
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
  MessageSquare,
  MessageSquarePlus,
  Moon,
  PauseCircle,
  Search,
  Square,
  Settings2,
  Sparkles,
  Sun,
  Trash2,
  User as UserIcon,
  UserPlus,
  Wrench,
  X,
  XCircle
} from "lucide-react";
import {
  cancelTask,
  ApiError,
  cancelToolApproval,
  checkAccessToken,
  clearAccessToken,
  clearAuthToken,
  createAgentTask,
  createConversation,
  createTaskSchedule,
  deleteConversation,
  getCurrentUser,
  getAuthStatus,
  getConversations,
  getDocuments,
  getMessages,
  getMcpPrompts,
  getMcpResources,
  getMcpServers,
  refreshMcpServers,
  getTasks,
  getTaskSchedules,
  getTools,
  login,
  logout,
  register,
  setAccessToken,
  setAuthToken,
  setTaskScheduleEnabled,
  streamChat,
  streamConfirmedTool,
  runTaskScheduleNow,
  updateConversationTitle,
  uploadDocument
} from "./api";
import { CopyButton, MessageContent } from "./features/chat/MessageContent";
import { useModalLayer } from "./lib/useModalLayer";
import { useMediaQuery } from "./lib/useMediaQuery";
import { StreamScope } from "./lib/streamScope";
import { readBrowserStorage, writeBrowserStorage } from "./lib/browserStorage";
import { TaskList } from "./features/tasks/TaskList";
import { DocumentCard } from "./features/knowledge/DocumentCard";
import { ScheduleComposer } from "./features/tasks/ScheduleComposer";
import { ScheduleList } from "./features/tasks/ScheduleList";
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
  TaskSchedule,
  TaskScheduleCreateInput,
  ToolManifest,
  User
} from "./types";

function providerLabel(provider: string | undefined): string {
  return provider === "mcp_server" ? "MCP" : "Local";
}

function countLabel(count: number, label: string): string {
  return `${count} ${label}${count === 1 ? "" : "s"}`;
}

type Theme = "light" | "dark";
type InspectorTab = "activity" | "context" | "tools";

const QUICK_PROMPTS = [
  {
    title: "Plan the next milestone",
    description: "Turn a broad goal into concrete, ordered work.",
    prompt: "Review the current project context and propose the next high-impact milestone with an execution plan."
  },
  {
    title: "Research with sources",
    description: "Find current information and synthesize the evidence.",
    prompt: "Research the latest practical patterns for reliable AI agents and summarize the findings with sources."
  },
  {
    title: "Create a recurring check",
    description: "Prepare a prompt that can run on a schedule.",
    prompt: "Review project health, identify regressions or blocked work, and recommend the next actions."
  }
] as const;

function initialTheme(): Theme {
  if (typeof window === "undefined") return "light";
    const stored = readBrowserStorage("agentdemo-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
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
  const [authRetry, setAuthRetry] = useState(0);
  const [bootstrapError, setBootstrapError] = useState("");
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [authError, setAuthError] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskError, setTaskError] = useState("");
  const [isLoadingTasks, setIsLoadingTasks] = useState(false);
  const [isCreatingTask, setIsCreatingTask] = useState(false);
  const [cancellingTaskIds, setCancellingTaskIds] = useState<Set<string>>(() => new Set());
  const [schedules, setSchedules] = useState<TaskSchedule[]>([]);
  const [scheduleError, setScheduleError] = useState("");
  const [isLoadingSchedules, setIsLoadingSchedules] = useState(false);
  const [isCreatingSchedule, setIsCreatingSchedule] = useState(false);
  const [isScheduleComposerOpen, setIsScheduleComposerOpen] = useState(false);
  const [updatingScheduleIds, setUpdatingScheduleIds] = useState<Set<string>>(
    () => new Set()
  );
  const [uploadStatus, setUploadStatus] = useState("No document uploaded in this chat.");
  const [uploadError, setUploadError] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [conversationToDelete, setConversationToDelete] = useState<Conversation | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(false);
  const [conversationQuery, setConversationQuery] = useState("");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("activity");
  const [executionTraces, setExecutionTraces] = useState<Record<string, ExecutionTrace>>({});
  const [confirmationDecisions, setConfirmationDecisions] = useState<Record<string, ConfirmationDecision>>({});
  const executionTraceRef = useRef<Record<string, ExecutionTrace>>({});
  const pendingConfirmationConversationRef = useRef<string | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const taskStatusRef = useRef<Record<string, string>>({});
  const streamScope = useRef(new StreamScope());
  const streamStopped = useRef(false);
  const followLatest = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const compactLayout = useMediaQuery("(max-width: 1100px)");
  const sidebarRef = useModalLayer<HTMLElement>(compactLayout && isSidebarOpen, () => setIsSidebarOpen(false));
  const inspectorRef = useModalLayer<HTMLElement>(compactLayout && isInspectorOpen, () => setIsInspectorOpen(false), ".mobile-inspector-button");
  const deleteDialogRef = useModalLayer<HTMLDivElement>(Boolean(conversationToDelete), () => setConversationToDelete(null));
  const conversationFilter = useDeferredValue(conversationQuery);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    writeBrowserStorage("agentdemo-theme", theme);
  }, [theme]);

  useEffect(() => () => streamScope.current.invalidate(), []);

  useEffect(() => {
    const area = textareaRef.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 200)}px`;
  }, [input, currentUser]);

  useEffect(() => {
    let active = true;
    setIsCheckingAuth(true);
    setBootstrapError("");
    async function restoreSession() {
      try {
        const accessStatus = await getAuthStatus();
        if (!active) return;
        setAccessRequired(accessStatus.required);
        if (accessStatus.required) {
          const isAllowed = await checkAccessToken();
          if (!isAllowed) {
            clearAccessToken();
            setHasAccess(false);
            setCurrentUser(null);
            return;
          }
        }
        setHasAccess(true);
        try {
          const user = await getCurrentUser();
          if (active) setCurrentUser(user);
        } catch (error) {
          if (!active) return;
          if (error instanceof ApiError && error.status === 401) {
            clearAuthToken();
            setCurrentUser(null);
          } else {
            throw error;
          }
        }
      } catch (error) {
        if (active) setBootstrapError(error instanceof Error ? error.message : "Service is unavailable");
      } finally {
        if (active) setIsCheckingAuth(false);
      }
    }
    void restoreSession();
    return () => { active = false; };
  }, [authRetry]);

  useEffect(() => {
    function expired() {
      resetWorkspace();
      setAuthError("Session expired. Please sign in again.");
    }
    function accessRequired() {
      resetWorkspace();
      clearAccessToken();
      setAccessRequired(true);
      setHasAccess(false);
    }
    window.addEventListener("agent-session-expired", expired);
    window.addEventListener("agent-access-required", accessRequired);
    return () => {
      window.removeEventListener("agent-session-expired", expired);
      window.removeEventListener("agent-access-required", accessRequired);
    };
  }, []);

  useEffect(() => {
    if (!currentUser) {
      setIsLoadingMcp(false);
      return;
    }
    let active = true;
    const failed = (error: unknown) => {
      if (active && !(error instanceof DOMException && error.name === "AbortError")) {
        setStatus(error instanceof Error ? error.message : "Could not load workspace");
      }
    };
    getConversations().then((items) => {
      if (!active) return;
      setConversations(items);
      setActiveConversationId(items[0]?.id ?? null);
    }).catch(failed);
    getTools().then((items) => { if (active) setTools(items); }).catch(failed);
    setIsLoadingMcp(true);
    Promise.all([getMcpServers(), getMcpResources(), getMcpPrompts()])
      .then(([servers, resources, prompts]) => {
        if (!active) return;
        setMcpServers(servers);
        setMcpResources(resources);
        setMcpPrompts(prompts);
        setMcpError("");
      })
      .catch((error) => { if (active) setMcpError(error instanceof Error ? error.message : "Failed to load MCP"); })
      .finally(() => { if (active) setIsLoadingMcp(false); });
    return () => { active = false; };
  }, [currentUser]);

  async function refreshMcp() {
    setIsLoadingMcp(true);
    setMcpError("");
    try {
      await refreshMcpServers();
      const [resources, prompts, availableTools] = await Promise.all([
        getMcpResources(), getMcpPrompts(), getTools()
      ]);
      setMcpServers(await getMcpServers());
      setMcpResources(resources);
      setMcpPrompts(prompts);
      setTools(availableTools);
    } catch (error) {
      setMcpError(error instanceof Error ? error.message : "Failed to refresh MCP");
    } finally {
      setIsLoadingMcp(false);
    }
  }

  useEffect(() => {
    if (isStreaming || streamStopped.current) {
      return;
    }
    if (!activeConversationId) {
      setMessages([]);
      setDocuments([]);
      setCitations([]);
      setTasks([]);
      setSchedules([]);
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
    let active = true;
    Promise.all([getMessages(activeConversationId), getDocuments(activeConversationId)]).then(
      ([loadedMessages, loadedDocuments]) => {
        if (!active) return;
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
    ).catch((error) => {
      if (active) setStatus(error instanceof Error ? error.message : "Could not load conversation");
    });
    return () => { active = false; };
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

  useLayoutEffect(() => {
    const list = messageListRef.current;
    if (!list) return;
    if (followLatest.current) list.scrollTop = list.scrollHeight;
  }, [messages, executionTraces]);

  useEffect(() => {
    if (!currentUser || !activeConversationId) {
      setTasks([]);
      setTaskError("");
      setIsLoadingTasks(false);
      setSchedules([]);
      setScheduleError("");
      setIsLoadingSchedules(false);
      return;
    }

    let isActive = true;
    async function loadConversationTasks(showLoading: boolean) {
      if (showLoading) {
        setIsLoadingTasks(true);
        setIsLoadingSchedules(true);
      }
      try {
        const [loadedTasks, loadedSchedules] = await Promise.all([
          getTasks(activeConversationId),
          getTaskSchedules(activeConversationId)
        ]);
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
        setSchedules(loadedSchedules);
        setTaskError("");
        setScheduleError("");
        if (completedTask && !isStreaming) {
          setStatus(taskStatusLabel(completedTask.status));
        }
      } catch (error) {
        if (!isActive) return;
        setTaskError(error instanceof Error ? error.message : "Failed to load tasks");
        setScheduleError(error instanceof Error ? error.message : "Failed to load schedules");
      } finally {
        if (isActive) {
          setIsLoadingTasks(false);
          setIsLoadingSchedules(false);
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

  const filteredConversations = useMemo(() => {
    const query = conversationFilter.trim().toLocaleLowerCase();
    if (!query) return conversations;
    return conversations.filter((conversation) =>
      conversation.title.toLocaleLowerCase().includes(query)
    );
  }, [conversationFilter, conversations]);

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
    if (isCreatingConversation) return;
    setIsCreatingConversation(true);
    streamScope.current.invalidate();
    streamStopped.current = false;
    setIsStreaming(false);
    try {
    const conversation = await createConversation();
    setConversations((current) => [conversation, ...current]);
    setActiveConversationId(conversation.id);
    setMessages([]);
    setCitations([]);
    setDocuments([]);
    setTasks([]);
    setSchedules([]);
    executionTraceRef.current = {};
    setExecutionTraces({});
    setConfirmationDecisions({});
    setUploadStatus("No document uploaded in this chat.");
    setUploadError("");
    setIsSidebarOpen(false);
    followLatest.current = true;
    setShowJumpToLatest(false);
    setInput("");
    setStatus("Ready");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not create conversation");
    } finally {
      setIsCreatingConversation(false);
    }
  }

  function handleSelectConversation(conversationId: string) {
    if (conversationId !== activeConversationId) {
      streamScope.current.invalidate();
      streamStopped.current = false;
      setIsStreaming(false);
      setMessages([]);
      setUploadError("");
      followLatest.current = true;
      setShowJumpToLatest(false);
      setStatus("Ready");
    }
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
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          clearAuthToken();
          setCurrentUser(null);
        } else {
          setBootstrapError(error instanceof Error ? error.message : "Service is unavailable");
        }
      }
    } catch (error) {
      clearAccessToken();
      setAccessError(error instanceof Error ? error.message : "Invalid access token");
    }
  }

  async function handleAuthSubmit(event: FormEvent) {
    event.preventDefault();
    const username = authUsername.trim();
    if (!username || !authPassword || isAuthenticating) return;
    setIsAuthenticating(true);
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
    } finally {
      setIsAuthenticating(false);
    }
  }

  async function handleLogout() {
    setIsLoggingOut(true);
    setLogoutError("");
    try {
      await logout();
      resetWorkspace();
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : "Could not revoke session. Retry logout.");
    } finally {
      setIsLoggingOut(false);
    }
  }

  function resetWorkspace() {
    setUploadError("");
    streamScope.current.invalidate();
    streamStopped.current = false;
    followLatest.current = true;
    setShowJumpToLatest(false);
    clearAuthToken();
    setCurrentUser(null);
    setConversations([]);
    setActiveConversationId(null);
    setMessages([]);
    setInput("");
    setIsStreaming(false);
    setTools([]);
    setDocuments([]);
    setTasks([]);
    setTaskError("");
    setSchedules([]);
    setScheduleError("");
    setCitations([]);
    executionTraceRef.current = {};
    setExecutionTraces({});
    setConfirmationDecisions({});
    setMcpServers([]);
    setMcpResources([]);
    setMcpPrompts([]);
    setLogoutError("");
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

  async function handleCreateSchedule(
    draft: Omit<TaskScheduleCreateInput, "prompt" | "conversation_id">
  ) {
    const text = input.trim();
    if (!text || isStreaming || isCreatingSchedule) return;
    setIsCreatingSchedule(true);
    setScheduleError("");
    setStatus("Creating schedule");
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
        setTasks([]);
        setSchedules([]);
        executionTraceRef.current = {};
        setExecutionTraces({});
        setConfirmationDecisions({});
      }
      const schedule = await createTaskSchedule({
        ...draft,
        prompt: text,
        conversation_id: conversationId
      });
      setSchedules((current) => [
        schedule,
        ...current.filter((item) => item.id !== schedule.id)
      ]);
      setInput("");
      setIsScheduleComposerOpen(false);
      setStatus("Schedule created");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to create schedule";
      setScheduleError(message);
      setStatus(message);
    } finally {
      setIsCreatingSchedule(false);
    }
  }

  async function handleToggleSchedule(schedule: TaskSchedule) {
    if (updatingScheduleIds.has(schedule.id)) return;
    setUpdatingScheduleIds((current) => new Set(current).add(schedule.id));
    setScheduleError("");
    try {
      const updated = await setTaskScheduleEnabled(schedule.id, !schedule.enabled);
      setSchedules((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      setStatus(updated.enabled ? "Schedule resumed" : "Schedule paused");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to update schedule";
      setScheduleError(message);
      setStatus(message);
    } finally {
      setUpdatingScheduleIds((current) => {
        const next = new Set(current);
        next.delete(schedule.id);
        return next;
      });
    }
  }

  async function handleRunScheduleNow(schedule: TaskSchedule) {
    if (updatingScheduleIds.has(schedule.id)) return;
    setUpdatingScheduleIds((current) => new Set(current).add(schedule.id));
    setScheduleError("");
    try {
      const task = await runTaskScheduleNow(schedule.id);
      taskStatusRef.current = { ...taskStatusRef.current, [task.id]: task.status };
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setStatus("Scheduled task queued now");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to run schedule";
      setScheduleError(message);
      setStatus(message);
    } finally {
      setUpdatingScheduleIds((current) => {
        const next = new Set(current);
        next.delete(schedule.id);
        return next;
      });
    }
  }

  async function handleCancelTool(tool: ToolTrace) {
    if (!activeConversationId || !tool.approval_id || isStreaming) return;
    try {
      await cancelToolApproval(activeConversationId, tool.approval_id);
      pendingConfirmationConversationRef.current = null;
      setConfirmationDecisions((current) => ({ ...current, [tool.id]: "cancelled" }));
      const loaded = await getMessages(activeConversationId);
      setMessages(loaded);
      executionTraceRef.current = {};
      setExecutionTraces({});
      setStatus("Tool approval cancelled");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to cancel tool approval");
    }
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

    const run = streamScope.current.begin();
    streamStopped.current = false;
    followLatest.current = true;
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
          approvalId: tool.approval_id,
          message: originalMessage,
          toolName: tool.tool_name,
          arguments: tool.arguments,
          reason: tool.reason ? `Confirmed: ${tool.reason}` : `Confirmed ${tool.tool_name}.`
        },
        (event) => handleRuntimeEvent(assistantMessageId, event, run.version), run.signal
      );
    } catch (error) {
      if (!streamScope.current.isCurrent(run.version)) return;
      streamStopped.current = run.signal.aborted;
      const message = run.signal.aborted ? "Generation stopped" : error instanceof Error ? error.message : "Failed to continue tool call";
      setStatus(message);
      updateExecutionTrace(assistantMessageId, (trace) => ({ ...trace, error: message }));
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && !item.content
            ? { ...item, content: run.signal.aborted ? message : `Request failed: ${message}` }
            : item
        )
      );
    } finally {
      if (streamScope.current.isCurrent(run.version)) setIsStreaming(false);
    }
  }

  function handleRuntimeEvent(assistantMessageId: string, event: StreamEvent, version: number) {
    if (!streamScope.current.isCurrent(version)) return;
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
      const existingTrace = executionTraceRef.current[assistantMessageId];
      const durableTrace = traceFromDoneEvent(event.data);
      const liveTrace = existingTrace ? {
        ...existingTrace, toolCalls: durableTrace.toolCalls
      } : durableTrace;
      updateExecutionTrace(assistantMessageId, () => liveTrace);
      const awaitingConfirmation = hasPendingConfirmation(liveTrace);
      setActiveConversationId(conversationId);
      setCitations(event.data.citations);
      setStatus(awaitingConfirmation ? "Waiting for tool confirmation" : "Done");
      pendingConfirmationConversationRef.current = awaitingConfirmation ? conversationId : null;
      void getConversations().then(async (items) => {
        if (!streamScope.current.isCurrent(version)) return;
        setConversations(items);
        const loadedDocuments = await getDocuments(conversationId);
        if (!streamScope.current.isCurrent(version)) return;
        if (awaitingConfirmation) {
          setDocuments(loadedDocuments);
          void getTasks(conversationId).then((items) => { if (streamScope.current.isCurrent(version)) setTasks(items); }).catch(() => undefined);
          return;
        }
        const loadedMessages = await getMessages(conversationId);
        if (!streamScope.current.isCurrent(version)) return;
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
        void getTasks(conversationId).then((items) => { if (streamScope.current.isCurrent(version)) setTasks(items); }).catch(() => undefined);
      }).catch((error) => {
        if (streamScope.current.isCurrent(version)) setStatus(error instanceof Error ? error.message : "Could not refresh conversation");
      });
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || isStreaming) return;
    const run = streamScope.current.begin();
    streamStopped.current = false;
    followLatest.current = true;
    setShowJumpToLatest(false);

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
      await streamChat(text, activeConversationId, (event) => handleRuntimeEvent(assistantMessageId, event, run.version), run.signal);
    } catch (error) {
      if (!streamScope.current.isCurrent(run.version)) return;
      streamStopped.current = run.signal.aborted;
      const message = run.signal.aborted ? "Generation stopped" : error instanceof Error ? error.message : "Request failed";
      setStatus(message);
      updateExecutionTrace(assistantMessageId, (trace) => ({ ...trace, error: message }));
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && !item.content
            ? { ...item, content: run.signal.aborted ? message : `Request failed: ${message}` }
            : item
        )
      );
    } finally {
      if (streamScope.current.isCurrent(run.version)) setIsStreaming(false);
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

  function handleQuickPrompt(prompt: string, openSchedule = false) {
    setInput(prompt);
    setIsScheduleComposerOpen(openSchedule);
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function handleUploadDocument(file: File | undefined) {
    if (!file || isUploading || isStreaming) return;
    setIsUploading(true);
    setUploadError("");
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
      setUploadError(error instanceof Error ? error.message : "Document upload failed");
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
    const needsApproval = trace?.toolCalls.some((tool) => tool.requires_confirmation && tool.status === "blocked"
      && !["cancelled", "confirmed"].includes(confirmationDecisions[tool.id] ?? ""));

    return (
      <details className="execution-trace" aria-label="Agent execution steps"
        open={Boolean(needsApproval) || Boolean(trace?.error) || !message.content}>
        <summary><Activity size={15} aria-hidden="true" /><span>
          {needsApproval ? "Permission needed" : trace?.error ? "Run interrupted" : "View agent activity"}
        </span><span className="trace-count">{trace?.toolCalls.length || ""}</span></summary>
        <div className="execution-trace-body">
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
        </div>
      </details>
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
        ) : tool.status === "success" || tool.status === "approved" ? (
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
                  disabled={isStreaming || !tool.approval_id}
                  onClick={() => void handleConfirmTool(messageId, tool)}
                >
                  <Check size={15} aria-hidden="true" />
                  Confirm
                </button>
                <button className="danger-action compact" type="button"
                  disabled={isStreaming || !tool.approval_id}
                  onClick={() => void handleCancelTool(tool)}>
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
          <div className="auth-brandmark"><Bot size={28} aria-hidden="true" /></div>
          <h1>Personal Agent</h1>
          <p className="auth-lead">Preparing your private agent workspace.</p>
          <p className="muted auth-loading"><Loader2 className="spin" size={15} aria-hidden="true" /> Checking session</p>
        </div>
      </main>
    );
  }

  if (bootstrapError) {
    return <main className="auth-screen"><div className="auth-panel">
      <h1>Workspace unavailable</h1>
      <p className="auth-lead">Your saved sign-in is preserved. Retry when the service is available.</p>
      <p className="auth-error" role="alert">{bootstrapError}</p>
      <button className="primary-action" onClick={() => setAuthRetry((value) => value + 1)}>Retry connection</button>
    </div></main>;
  }

  if (accessRequired && !hasAccess) {
    return (
      <main className="auth-screen">
        <form className="auth-panel" onSubmit={handleAccessSubmit}>
          <div className="auth-brandmark"><Bot size={28} aria-hidden="true" /></div>
          <p className="eyebrow">Private workspace</p>
          <h1>Personal Agent</h1>
          <p className="auth-lead">Enter the project token to continue to your local-first workspace.</p>
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
          <div className="auth-brandmark"><Bot size={28} aria-hidden="true" /></div>
          <p className="eyebrow">Local-first intelligence</p>
          <h1>Personal Agent</h1>
          <p className="auth-lead">Plan, research, use tools, and keep work moving in one focused workspace.</p>
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
            autoComplete={authMode === "register" ? "new-password" : "current-password"}
            minLength={authMode === "register" ? 15 : 4}
            maxLength={128}
          />
          {authMode === "register" ? <p className="muted">Use a passphrase of at least 15 characters.</p> : null}
          {authError ? <p className="auth-error" role="alert">{authError}</p> : null}
          <button className="primary-action" disabled={isAuthenticating}>
            {authMode === "login" ? <LogIn size={18} aria-hidden="true" /> : <UserPlus size={18} aria-hidden="true" />}
            {isAuthenticating ? "Signing in…" : authMode === "login" ? "Login" : "Create account"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <main className={isInspectorOpen ? "app-shell inspector-visible" : "app-shell"}>
      <a className="skip-link" href="#chat-input">Skip to message composer</a>
      <aside ref={sidebarRef} className={isSidebarOpen ? "sidebar mobile-open" : "sidebar"} aria-label="Conversations"
        role={compactLayout && isSidebarOpen ? "dialog" : undefined}
        aria-modal={compactLayout && isSidebarOpen ? true : undefined}
        inert={Boolean(conversationToDelete) || (compactLayout && !isSidebarOpen)}>
        <div className="brand">
          <div className="brand-mark"><Asterisk size={29} strokeWidth={1.8} aria-hidden="true" /></div>
          <div>
            <h1>AgentDemo</h1>
            <p>Your thinking space</p>
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
        <button className="primary-action new-chat-action" onClick={handleNewConversation} disabled={isCreatingConversation}>
          <MessageSquarePlus size={18} aria-hidden="true" />
          New chat
        </button>
        <div className="conversation-search">
          <Search size={16} aria-hidden="true" />
          <label className="sr-only" htmlFor="conversation-search">Search conversations</label>
          <input
            id="conversation-search"
            type="search"
            value={conversationQuery}
            onChange={(event) => setConversationQuery(event.target.value)}
            placeholder="Search conversations"
          />
        </div>
        <div className="sidebar-section-heading">
          <span>Recent</span>
          <span>{filteredConversations.length}</span>
        </div>
        <nav className="conversation-list" aria-label="Recent conversations">
          {filteredConversations.length === 0 ? (
            <p className="sidebar-empty">{conversationQuery ? "No matching conversations." : "Start a new chat to build your workspace."}</p>
          ) : filteredConversations.map((conversation) => (
            <div
              className={conversation.id === activeConversationId ? "conversation active" : "conversation"}
              key={conversation.id}
            >
              <button className="conversation-select" title={conversation.title}
                aria-current={conversation.id === activeConversationId ? "page" : undefined}
                onClick={() => handleSelectConversation(conversation.id)}>
                <MessageSquare size={16} aria-hidden="true" />
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
        <div className="sidebar-footer">
          <button className="sidebar-logout sidebar-theme" type="button" onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            {theme === "dark" ? "Light appearance" : "Dark appearance"}
          </button>
          <div className="user-chip" title={`Signed in as ${currentUser.username}`}>
            <UserIcon size={17} aria-hidden="true" />
            <div><span>Signed in as</span><strong>{currentUser.username}</strong></div>
          </div>
          {logoutError ? <p className="inline-error" role="alert">{logoutError}</p> : null}
          <button className="sidebar-logout" type="button" onClick={handleLogout} disabled={isLoggingOut}>
            <LogOut size={17} aria-hidden="true" />
            {isLoggingOut ? "Signing out…" : "Log out"}
          </button>
        </div>
      </aside>

      <section className="workspace" aria-label="Chat workspace"
        inert={Boolean(conversationToDelete)}>
        <header className="topbar" inert={compactLayout && (isSidebarOpen || isInspectorOpen)}>
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
              className="icon-button theme-toggle"
              type="button"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
            </button>
            <button
              className="icon-button mobile-inspector-button"
              type="button"
              aria-label={isInspectorOpen ? "Close runtime details" : "Open runtime details"}
              title="Runtime, sources and tools"
              aria-expanded={isInspectorOpen}
              onClick={() => {
                setIsSidebarOpen(false);
                setIsInspectorOpen((current) => !current);
              }}
            >
              <Settings2 size={18} aria-hidden="true" />
              <span className="runtime-button-label">Details</span>
            </button>
            <div className={isStreaming ? "runtime-state working" : "runtime-state"} aria-live="polite">
              {isStreaming ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <CircleDot size={16} />}
              {status}
            </div>
          </div>
        </header>

        <div className="content-grid">
          <section className="chat-panel" aria-label="Messages" inert={compactLayout && (isSidebarOpen || isInspectorOpen)}>
            <div className="message-list" ref={messageListRef} onScroll={(event) => {
              const list = event.currentTarget;
              const nearEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
              followLatest.current = nearEnd;
              setShowJumpToLatest(!nearEnd);
            }}>
              {messages.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-mark"><Asterisk size={52} strokeWidth={1.4} aria-hidden="true" /></div>
                  <p className="eyebrow">A little space to think</p>
                  <h3>What would you like<br />to work on?</h3>
                  <p>Bring a question, a document, or an idea.<br />We’ll take it from here, together.</p>
                  <div className="quick-prompts" aria-label="Suggested prompts">
                    {QUICK_PROMPTS.map((item, index) => (
                      <button
                        className="quick-prompt"
                        type="button"
                        key={item.title}
                        onClick={() => handleQuickPrompt(item.prompt, index === 2)}
                      >
                        <span>{item.title}</span>
                        <small>{item.description}</small>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                messages.map((message) => (
                  <article className={`message ${message.role}`} key={message.id}>
                    <div className="message-header">
                      <span className="message-avatar" aria-hidden="true">
                        {message.role === "assistant" ? <Asterisk size={23} /> : <UserIcon size={15} />}
                      </span>
                      <div className="message-role">{message.role === "assistant" ? "Agent" : "You"}</div>
                    </div>
                    {message.role === "assistant" ? renderExecutionTrace(message) : null}
                    {message.content ? message.role === "assistant"
                      ? <MessageContent text={message.content} />
                      : <p className="user-message-text">{message.content}</p> : null}
                    {message.role === "assistant" && message.content && !isStreaming
                      ? <div className="message-actions"><CopyButton text={message.content} /></div> : null}
                  </article>
                ))
              )}
            </div>
            {showJumpToLatest && <button className="jump-latest" type="button" onClick={() => {
              followLatest.current = true;
              if (messageListRef.current) messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
              setShowJumpToLatest(false);
            }}><ArrowDown size={16} /> Latest</button>}
            {isStreaming && <div className="stream-status" role="status"><Loader2 className="spin" size={14} />{status}</div>}
            <form className="composer" onSubmit={handleSubmit}>
              <label className="sr-only" htmlFor="chat-input">Message</label>
              <div className="composer-input-shell">
                <textarea
                  ref={textareaRef}
                  id="chat-input"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder="How can I help you today?"
                  rows={2}
                />
                <div className="composer-hint" aria-hidden="true">
                  <span>Enter to send · Shift + Enter for a new line</span>
                </div>
              </div>
              <ScheduleComposer
                open={isScheduleComposerOpen}
                disabled={isStreaming || !input.trim()}
                isCreating={isCreatingSchedule}
                error={scheduleError}
                onClose={() => setIsScheduleComposerOpen(false)}
                onCreate={handleCreateSchedule}
              />
              <div className="composer-toolbar">
                <div className="upload-row">
                  <input
                    ref={fileInputRef}
                    id="document-upload"
                    type="file"
                    disabled={isUploading || isStreaming}
                    accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
                    onChange={(event) => handleUploadDocument(event.target.files?.[0])}
                  />
                  <label className="upload-button" htmlFor="document-upload" title="Attach a document">
                    <FileUp size={18} aria-hidden="true" />
                    {isUploading ? "Indexing..." : "Attach"}
                  </label>
                </div>
                <div className="composer-actions">
                  <button
                    className="background-task-button"
                    type="button"
                    aria-expanded={isScheduleComposerOpen}
                    disabled={isStreaming || isCreatingTask || !input.trim()}
                    onClick={() => setIsScheduleComposerOpen((current) => !current)}
                  >
                    <CalendarClock size={18} aria-hidden="true" />
                    Schedule
                  </button>
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
                    {isCreatingTask ? "Queueing" : "Background"}
                  </button>
                  {isStreaming ? <button className="send-button stop-button" type="button" aria-label="Stop generating"
                    title="Stop generating" onClick={() => streamScope.current.stop()}><Square size={17} fill="currentColor" /></button>
                    : <button className="send-button" aria-label="Send" title="Send message" disabled={isCreatingTask || !input.trim()}>
                      <ArrowUp size={21} aria-hidden="true" />
                    </button>}
                </div>
              </div>
              <div className="composer-footer">
                <span role="status">{isUploading ? uploadStatus : documents.length ? `${documents.length} document${documents.length === 1 ? "" : "s"} attached` : "Your workspace, your context."}</span>
                {(documents.length > 0 || citations.length > 0) && <button className="text-link" type="button" onClick={() => {
                  setInspectorTab("context"); setIsInspectorOpen(true); setIsSidebarOpen(false);
                }}>View sources <BookOpen size={14} /></button>}
              </div>
              {uploadError ? <p className="inline-error" role="alert">{uploadError}</p> : null}
            </form>
          </section>

          <aside ref={inspectorRef} className={isInspectorOpen ? "inspector mobile-open" : "inspector"} aria-label="Runtime inspector"
            role={compactLayout && isInspectorOpen ? "dialog" : undefined}
            aria-modal={compactLayout && isInspectorOpen ? true : undefined}
            inert={!isInspectorOpen || Boolean(conversationToDelete)}>
            <div className="inspector-header">
              <div>
                <p className="eyebrow">Behind the conversation</p>
                <h2>Workspace details</h2>
              </div>
              <button
                className="icon-button inspector-close"
                type="button"
                aria-label="Close runtime details"
                title="Close runtime details"
                onClick={() => setIsInspectorOpen(false)}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="inspector-overview" aria-label="Runtime summary">
              <div><span>State</span><strong>{isStreaming ? "Working" : "Ready"}</strong></div>
              <div><span>Tasks</span><strong>{tasks.length}</strong></div>
              <div><span>Tools</span><strong>{tools.length}</strong></div>
            </div>
            <div className="inspector-tabs" role="group" aria-label="Runtime views">
              <button
                className={inspectorTab === "activity" ? "active" : ""}
                type="button"
                aria-pressed={inspectorTab === "activity"}
                onClick={() => setInspectorTab("activity")}
              >
                <Activity size={16} aria-hidden="true" /> Activity
              </button>
              <button
                className={inspectorTab === "context" ? "active" : ""}
                type="button"
                aria-pressed={inspectorTab === "context"}
                onClick={() => setInspectorTab("context")}
              >
                <BookOpen size={16} aria-hidden="true" /> Context
              </button>
              <button
                className={inspectorTab === "tools" ? "active" : ""}
                type="button"
                aria-pressed={inspectorTab === "tools"}
                onClick={() => setInspectorTab("tools")}
              >
                <Wrench size={16} aria-hidden="true" /> Tools
              </button>
            </div>
            <section hidden={inspectorTab !== "activity"}>
              <div className="section-title">
                <CalendarClock size={18} aria-hidden="true" />
                <h3>Schedules</h3>
              </div>
              <ScheduleList
                schedules={schedules}
                error={scheduleError}
                isLoading={isLoadingSchedules}
                updatingIds={updatingScheduleIds}
                onToggle={(schedule) => void handleToggleSchedule(schedule)}
                onRunNow={(schedule) => void handleRunScheduleNow(schedule)}
              />
            </section>
            <section hidden={inspectorTab !== "activity"}>
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
            <section hidden={inspectorTab !== "context"}>
              <div className="section-title">
                <FileUp size={18} aria-hidden="true" />
                <h3>Documents</h3>
              </div>
              <div className="stack-list">
                {documents.length === 0 ? (
                  <p className="muted">Upload TXT, Markdown, or text PDF files for this chat.</p>
                ) : (
                  documents.map((document) => (
                    <DocumentCard key={document.id} document={document} onUpdated={(updated) =>
                      setDocuments((current) => current.map((item) => item.id === updated.id ? updated : item))
                    } />
                  ))
                )}
              </div>
            </section>

            <section hidden={inspectorTab !== "tools"}>
              <div className="section-title">
                <Settings2 size={18} aria-hidden="true" />
                <h3>MCP</h3>
                <button type="button" className="icon-button text-button mcp-refresh" onClick={() => void refreshMcp()} disabled={isLoadingMcp}>
                  {isLoadingMcp ? "Checking…" : "Refresh MCP"}
                </button>
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
                        {server.protocol_version ? <span>Protocol {server.protocol_version}</span> : null}
                        {server.last_checked_at ? <span>Last checked {new Date(server.last_checked_at).toLocaleString()}</span> : null}
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

            <section hidden={inspectorTab !== "tools"}>
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

            <section hidden={inspectorTab !== "tools"}>
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

            <section hidden={inspectorTab !== "context"}>
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
                      <span>Chunk {citation.chunk_index + 1} · {citation.retrieval_method === "hybrid_rrf" ? "Hybrid (RRF)" : citation.retrieval_method === "vector" ? "Semantic" : citation.retrieval_method === "keyword" ? "Keyword" : "Document context"}</span>
                      {citation.metadata?.selection_method === "mmr" && <span>Diverse evidence · MMR</span>}
                      {citation.metadata?.reranker && <span>Reranked · {citation.metadata.reranker}</span>}
                      {citation.metadata?.rerank_status === "fallback" && <span>Reranker unavailable · local ranking</span>}
                      {citation.metadata?.retrieval_context && <span>{citation.metadata.retrieval_context}</span>}
                      {citation.source_uri && <span>{citation.source_uri}</span>}
                      {citation.metadata?.start_char != null && citation.metadata.end_char != null && (
                        <span>Text position {citation.metadata.start_char + 1}–{citation.metadata.end_char}</span>
                      )}
                      <p>{citation.content}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section hidden={inspectorTab !== "tools"}>
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
          <div ref={deleteDialogRef}
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
