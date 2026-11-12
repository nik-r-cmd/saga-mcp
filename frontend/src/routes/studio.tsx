import { useCallback, useEffect, useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Activity,
  AlertCircle,
  Check,
  CircleCheck,
  CircleX,
  Copy,
  Github,
  GitBranch,
  Loader2,
  LogOut,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { supabase } from "@/integrations/supabase/client";
import { lovable } from "@/integrations/lovable";
import {
  approveTool,
  discoverTools,
  getRegistry,
  removeTool,
  type ApprovedEntry,
  type DiscoveredTool,
} from "@/lib/studio/api";
import {
  BACKEND_URL,
  beginGitHubConnect,
  getGitHubConnectionStatus,
  getAgentStatus,
  getWebSocketTicket,
  runRollbackDemo,
  runSagaTask,
  scanGitHubRepo,
  toolsFromScan,
  websocketUrl,
} from "@/lib/studio/connected-api";

const TITLE = "Saga-MCP — Control Plane";
const DESC = "Connect your systems, approve safe actions, and watch agent work as it happens.";
const CATEGORIES = [
  { value: "compensable", label: "Compensable" },
  { value: "pivot", label: "Pivot / Irreversible" },
];
const EVENT_STYLES: Record<string, string> = {
  FORWARD_EXEC: "border-success/40 bg-success/10 text-success",
  FAULT_DETECTED: "border-destructive/40 bg-destructive/10 text-destructive",
  COMPENSATED: "border-border bg-surface-2 text-muted-foreground line-through",
  DEAD_LETTER: "border-destructive/60 bg-destructive/15 text-destructive",
};

export const Route = createFileRoute("/studio")({
  head: () => ({
    meta: [
      { title: TITLE },
      { name: "description", content: DESC },
      { property: "og:title", content: TITLE },
      { property: "og:description", content: DESC },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Studio,
});

interface RowState {
  category: string;
  pairing: string;
  compensationIdempotent: boolean;
  approved: boolean;
  busy: boolean;
}
interface ToolServer {
  command: string;
  args: string[];
}
interface StreamEvent {
  event_type?: string;
  type?: string;
  timestamp?: string | number;
  step_id?: string;
  tool_name?: string;
  status?: string;
  [key: string]: unknown;
}
interface SagaSummary {
  saga_id?: string;
  status?: string;
  error?: string;
  events?: StreamEvent[];
  steps?: Array<{
    tool_name: string;
    status: string;
    failure_reason?: string;
    failure_detail?: string;
  }>;
}
type StreamEnvelope = { event?: unknown; data?: unknown; payload?: unknown };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStreamEvent(value: unknown): value is StreamEvent {
  return isRecord(value);
}

function mergeStreamEvents(current: StreamEvent[], incoming: StreamEvent[]): StreamEvent[] {
  const merged = new Map<string, StreamEvent>();
  for (const event of [...current, ...incoming]) {
    const key = `${String(event.step_id ?? event.tool_name ?? "")}:${String(event.event_type ?? event.type ?? "")}`;
    merged.set(key, event);
  }
  return [...merged.values()].slice(-100);
}

function normalizeStreamEvent(value: unknown): StreamEvent | null {
  if (!isRecord(value)) return null;
  const envelope = value as StreamEnvelope;
  const candidate = envelope.event ?? envelope.data ?? envelope.payload ?? value;
  return isStreamEvent(candidate) ? candidate : null;
}

function normalizeCategory(raw: string) {
  const value = (raw || "").toLowerCase();
  return value.includes("pivot") || value.includes("irrevers") ? "pivot" : "compensable";
}

function compensationMappings(
  tool: DiscoveredTool,
  tools: DiscoveredTool[],
): { arguments: Record<string, string>; result: Record<string, string> } | null {
  if (normalizeCategory(tool.suggested_category) === "pivot" || !tool.suggested_compensating_tool)
    return null;
  const inverse = tools.find((candidate) => candidate.name === tool.suggested_compensating_tool);
  if (!inverse) return null;
  const forwardProperties = tool.input_schema?.properties ?? {};
  const required = inverse.input_schema?.required ?? [];
  const forwardRequired = tool.input_schema?.required ?? [];
  const argumentMapping: Record<string, string> = {};
  const resultMapping: Record<string, string> = {};
  for (const name of required) {
    if (Object.hasOwn(forwardProperties, name) && forwardRequired.includes(name)) {
      argumentMapping[name] = name;
    } else if (
      tool.name === "commit_file" &&
      inverse.name === "revert_commit" &&
      name === "commit_sha"
    ) {
      resultMapping[name] = "commit_sha";
    } else if (
      tool.name === "seed_database" &&
      inverse.name === "wipe_database" &&
      name === "record_ids"
    ) {
      resultMapping[name] = "record_ids";
    } else {
      return null;
    }
  }
  return { arguments: argumentMapping, result: resultMapping };
}

function Studio() {
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [githubConnected, setGithubConnected] = useState(false);
  const [githubStatusError, setGithubStatusError] = useState<string | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [servers, setServers] = useState<unknown[]>([]);
  const scannedServersRef = useRef<unknown[]>([]);
  const [agentHint, setAgentHint] = useState<string | null>(null);
  const [tools, setTools] = useState<DiscoveredTool[] | null>(null);
  const [toolServers, setToolServers] = useState<Record<string, ToolServer>>({});
  const [command, setCommand] = useState("python");
  const [args, setArgs] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [approved, setApproved] = useState<ApprovedEntry[]>([]);
  const [task, setTask] = useState("");
  const [taskBusy, setTaskBusy] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [taskNeedsAgent, setTaskNeedsAgent] = useState(false);
  const [demoRepoPath, setDemoRepoPath] = useState("");
  const [demoError, setDemoError] = useState<string | null>(null);
  const [agentState, setAgentState] = useState<"offline" | "connecting" | "connected">("offline");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [sagaSummary, setSagaSummary] = useState<SagaSummary | null>(null);
  const taskTimeoutRef = useRef<number | null>(null);
  const [activeTab, setActiveTab] = useState<"workspace" | "registry">("workspace");

  const refreshRegistry = useCallback(async () => {
    try {
      const data = await getRegistry();
      setApproved(data.approved ?? []);
    } catch {
      /* API may be unavailable before setup */
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (mounted) setSessionToken(data.session?.access_token ?? null);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, current) =>
      setSessionToken(current?.access_token ?? null),
    );
    return () => {
      mounted = false;
      data.subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (sessionToken) void refreshRegistry();
  }, [sessionToken, refreshRegistry]);

  useEffect(() => {
    const receiveTaskResult = (event: Event) => {
      const result = (event as CustomEvent<SagaSummary>).detail;
      if (!result) return;
      if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
      taskTimeoutRef.current = null;
      setTaskBusy(false);
      setSagaSummary(result);
      if (result.events?.length) {
        setEvents((current) => mergeStreamEvents(current, result.events ?? []));
      }
      setTaskError(
        result.status === "plan_rejected" || result.status === "agent_error"
          ? (result.error ?? "The local agent could not complete the task.")
          : null,
      );
      setDemoError(
        result.status === "demo_error" ? (result.error ?? "The rollback demo failed.") : null,
      );
    };
    window.addEventListener("saga-task-result", receiveTaskResult);
    return () => window.removeEventListener("saga-task-result", receiveTaskResult);
  }, []);

  useEffect(() => {
    if (!sessionToken) {
      setAgentState("offline");
      return;
    }
    let active = true;
    let checked = false;
    const refreshStatus = async () => {
      if (!checked) setAgentState("connecting");
      try {
        const status = await getAgentStatus(sessionToken);
        if (active) setAgentState(status.connected ? "connected" : "offline");
      } catch {
        if (active) setAgentState("offline");
      } finally {
        checked = true;
      }
    };
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [sessionToken]);

  useEffect(() => {
    if (!sessionToken) return;
    let active = true;
    let socket: WebSocket | null = null;
    const handleMessage = (message: MessageEvent) => {
      try {
        const parsed: unknown = JSON.parse(message.data);
        if (isRecord(parsed) && parsed["type"] === "task_result") {
          const summary = parsed as unknown as SagaSummary;
          if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
          taskTimeoutRef.current = null;
          setTaskBusy(false);
          setSagaSummary(summary);
          if (summary.events?.length) {
            setEvents((current) => mergeStreamEvents(current, summary.events ?? []));
          }
          if (summary.status === "demo_error")
            setDemoError(summary.error ?? "The rollback demo failed.");
          if (summary.status === "plan_rejected" || summary.status === "agent_error") {
            setTaskError(
              summary.error ?? "The local agent could not produce or execute a valid plan.",
            );
          }
        }
        if (
          isRecord(parsed) &&
          parsed["type"] === "discovery_result" &&
          isRecord(parsed["tools_by_server"])
        ) {
          const serverConfigs = scannedServersRef.current.filter(isRecord);
          for (const [serverName, result] of Object.entries(parsed["tools_by_server"])) {
            const config = serverConfigs.find((server) => server["server_name"] === serverName);
            if (!config || !Array.isArray(result)) continue;
            const discovered = result
              .filter(isRecord)
              .map((tool) => tool as unknown as DiscoveredTool);
            applyTools(discovered, {
              command: String(config["command"] ?? ""),
              args: Array.isArray(config["args"]) ? config["args"].map(String) : [],
            });
          }
        }
        const incoming = normalizeStreamEvent(parsed);
        if (incoming?.event_type) setEvents((current) => [incoming, ...current].slice(0, 100));
      } catch {
        /* ignore malformed events */
      }
    };
    void getWebSocketTicket(sessionToken)
      .then(({ ticket }) => {
        if (!active) return;
        socket = new WebSocket(websocketUrl(ticket));
        socket.onmessage = handleMessage;
      })
      .catch(() => {
        if (active) setAgentState("offline");
      });
    return () => {
      active = false;
      socket?.close();
      if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
    };
  }, [sessionToken]);

  useEffect(() => {
    if (!sessionToken) {
      setGithubConnected(false);
      setGithubStatusError(null);
      return;
    }
    let active = true;
    void getGitHubConnectionStatus(sessionToken)
      .then(({ connected }) => {
        if (active) {
          setGithubConnected(connected);
          setGithubStatusError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setGithubConnected(false);
          setGithubStatusError(
            error instanceof Error
              ? `Could not check GitHub connection: ${error.message}`
              : "Could not check GitHub connection status.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [sessionToken]);

  const setRow = (name: string, patch: Partial<RowState>) =>
    setRows((current) => {
      const row = current[name];
      return row ? { ...current, [name]: { ...row, ...patch } } : current;
    });
  const applyTools = (list: DiscoveredTool[], server?: ToolServer) => {
    setTools((current) => {
      const byName = new Map((current ?? []).map((tool) => [tool.name, tool]));
      list.forEach((tool) => byName.set(tool.name, tool));
      return [...byName.values()];
    });
    setRows((current) => ({
      ...current,
      ...Object.fromEntries(
        list.map((tool) => [
          tool.name,
          {
            category: normalizeCategory(tool.suggested_category),
            pairing: tool.suggested_compensating_tool ?? "",
            compensationIdempotent: false,
            approved: tool.already_approved,
            busy: false,
          },
        ]),
      ),
    }));
    if (server)
      setToolServers((current) => ({
        ...current,
        ...Object.fromEntries(list.map((tool) => [tool.name, server])),
      }));
  };

  const onAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError(null);
    try {
      const result =
        authMode === "signin"
          ? await supabase.auth.signInWithPassword({ email, password })
          : await supabase.auth.signUp({ email, password });
      if (result.error) throw result.error;
      if (authMode === "signup" && !result.data.session)
        setAuthError("Check your email to confirm your account, then return here.");
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setAuthBusy(false);
    }
  };
  const onGoogle = async () => {
    setAuthError(null);
    const result = await lovable.auth.signInWithOAuth("google", {
      redirect_uri: window.location.origin,
    });
    if (result.error) setAuthError(result.error.message);
  };
  const onGitHubConnect = async () => {
    if (!sessionToken) return;
    setScanError(null);
    try {
      const { authorization_url } = await beginGitHubConnect(sessionToken);
      window.location.assign(authorization_url);
    } catch (error) {
      setScanError(
        error instanceof Error ? error.message : "Could not start GitHub authorization.",
      );
    }
  };
  const signOut = async () => {
    await supabase.auth.signOut();
    setSessionToken(null);
  };

  const onScan = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!sessionToken) return;
    setScanBusy(true);
    setScanError(null);
    setAgentHint(null);
    try {
      const result = await scanGitHubRepo(owner.trim(), repo.trim(), sessionToken);
      const foundServers = result.servers_found ?? [];
      scannedServersRef.current = foundServers;
      setServers(foundServers);
      if (result.discovery === "agent_not_connected")
        setAgentHint(result.setup_url ?? result.setup_instructions_url ?? "#local-agent");
      const discovered = toolsFromScan(result);
      if (discovered.length) applyTools(discovered);
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "The repository could not be scanned.");
    } finally {
      setScanBusy(false);
    }
  };
  const onDiscover = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    const formattedArgs = args
      .split(/[,\r\n]+/)
      .map((arg) => arg.trim())
      .filter(Boolean);
    try {
      const data = await discoverTools(command.trim(), formattedArgs);
      applyTools(data.tools ?? [], { command: command.trim(), args: formattedArgs });
      void refreshRegistry();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };
  const onApprove = async (tool: DiscoveredTool) => {
    const row = rows[tool.name];
    if (!row) return;
    const server = toolServers[tool.name];
    if (!server) {
      setError(`Rediscover ${tool.name} locally so its MCP server configuration can be saved.`);
      return;
    }
    const mappings = row.category === "pivot" ? null : compensationMappings(tool, tools ?? []);
    if (row.category === "compensable" && mappings === null) {
      setError(`No safe argument mapping could be inferred for ${tool.name}.`);
      return;
    }
    setRow(tool.name, { busy: true });
    setError(null);
    try {
      await approveTool({
        tool_name: tool.name,
        category: row.category,
        compensating_tool: row.category === "pivot" ? null : row.pairing || null,
        server_command: server.command,
        server_args: server.args,
        compensation_arg_mapping: mappings?.arguments ?? null,
        compensation_result_arg_mapping: mappings?.result ?? null,
        compensation_idempotent: row.compensationIdempotent,
        tool_description: tool.description,
        input_schema: tool.input_schema ?? {},
      });
      setRow(tool.name, { approved: true, busy: false });
      void refreshRegistry();
    } catch (err) {
      setRow(tool.name, { busy: false });
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const onRemove = async (name: string) => {
    try {
      await removeTool(name);
      setRow(name, { approved: false });
      void refreshRegistry();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const onRun = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!sessionToken || !task.trim() || taskBusy) return;
    setTaskBusy(true);
    setTaskError(null);
    setTaskNeedsAgent(false);
    setEvents([]);
    setSagaSummary(null);
    beginTaskWait();
    const formattedArgs = args
      .split(/[,\r\n]+/)
      .map((arg) => arg.trim())
      .filter(Boolean);
    try {
      await runSagaTask(
        { task_description: task.trim(), mcp_command: command.trim(), mcp_args: formattedArgs },
        sessionToken,
      );
    } catch (err) {
      if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
      taskTimeoutRef.current = null;
      const typed = err as Error & { status?: number };
      const needsAgent = typed.status === 409;
      setTaskNeedsAgent(needsAgent);
      setTaskError(needsAgent ? "No local agent connected - start it first" : typed.message);
      setTaskBusy(false);
    }
  };
  const onRunRollbackDemo = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!sessionToken || taskBusy) return;
    if (!demoRepoPath.trim()) {
      setDemoError("Enter the path to a local Git repository.");
      return;
    }
    setTaskBusy(true);
    setDemoError(null);
    setTaskError(null);
    setEvents([]);
    setSagaSummary(null);
    beginTaskWait();
    try {
      await runRollbackDemo(demoRepoPath.trim(), sessionToken);
    } catch (err) {
      if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
      taskTimeoutRef.current = null;
      const typed = err as Error & { status?: number };
      setTaskNeedsAgent(typed.status === 409);
      setDemoError(typed.message);
      setTaskBusy(false);
    }
  };
  const configuredBackend = new URL(BACKEND_URL);
  const backendPort = configuredBackend.port || "8000";
  configuredBackend.protocol = configuredBackend.protocol === "https:" ? "wss:" : "ws:";
  configuredBackend.pathname = "";
  configuredBackend.search = "";
  const backendWebSocketUrl = `${configuredBackend.protocol}//${configuredBackend.host}`;
  const backendSetupCommand = [
    'Set-Location "<path-to-saga-mcp>\\backend"',
    `..\\.venv\\Scripts\\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port ${backendPort}`,
  ].join("\n");
  const setupCommand = [
    'Set-Location "<path-to-saga-mcp>\\backend"',
    `$env:SAGA_BACKEND_HTTP_URL = "${BACKEND_URL}"`,
    `$env:SAGA_BACKEND_WS_URL = "${backendWebSocketUrl}"`,
    '$env:SAGA_SUPABASE_TOKEN = "<paste your Supabase access_token>"',
    "..\\.venv\\Scripts\\python.exe -m agent.saga_agent",
  ].join("\n");
  const latestEvent = events[0];
  const statusLabel =
    agentState === "connected"
      ? "Connected"
      : agentState === "connecting"
        ? "Connecting"
        : "Offline";
  const eventCount = events.length;
  const beginTaskWait = () => {
    if (taskTimeoutRef.current !== null) window.clearTimeout(taskTimeoutRef.current);
    taskTimeoutRef.current = window.setTimeout(() => {
      setTaskBusy(false);
      setTaskError("Timed out waiting for the local agent result.");
      setDemoError("Timed out waiting for the local agent result.");
      taskTimeoutRef.current = null;
    }, 120_000);
  };

  if (!sessionToken)
    return (
      <AuthWall
        email={email}
        setEmail={setEmail}
        password={password}
        setPassword={setPassword}
        authMode={authMode}
        setAuthMode={setAuthMode}
        authBusy={authBusy}
        authError={authError}
        onAuth={onAuth}
        onGoogle={onGoogle}
      />
    );

  return (
    <main className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b border-border/80 bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-5 px-5 lg:px-8">
          <Link
            to="/"
            className="font-mono text-sm font-semibold tracking-[0.16em] text-foreground"
          >
            SAGA<span className="text-warn">·</span>MCP
          </Link>
          <span className="hidden border-l border-border pl-5 text-sm text-muted-foreground sm:block">
            Control Plane
          </span>
          <div className="ml-auto flex items-center gap-3">
            <AgentStatus state={agentState} label={statusLabel} />
            <Button variant="ghost" size="sm" onClick={() => void signOut()}>
              <LogOut className="size-4" /> <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </div>
      </header>
      <div className="mx-auto max-w-7xl px-5 py-8 lg:px-8">
        <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Workspace
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
              Keep agent work visible and reversible.
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              Connect a repository, review the actions it exposes, and run tasks with a live record
              of what happened.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setActiveTab("workspace")}
              className={activeTab === "workspace" ? "border-warn/60 text-warn" : ""}
            >
              Workspace
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setActiveTab("registry")}
              className={activeTab === "registry" ? "border-warn/60 text-warn" : ""}
            >
              Approved registry{" "}
              <Badge variant="secondary" className="ml-1">
                {approved.length}
              </Badge>
            </Button>
          </div>
        </div>
        {activeTab === "workspace" ? (
          <>
            <div className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
              <section className="border border-border bg-card p-5">
                <SectionHeading
                  icon={Github}
                  title="Connect a repository"
                  description="Link GitHub and choose the repository Saga-MCP should inspect."
                />
                <div className="mt-5 flex flex-wrap gap-3">
                  <Button onClick={() => void onGitHubConnect()} variant="outline">
                    <Github className="size-4" />{" "}
                    {githubConnected ? "Reconnect GitHub" : "Connect GitHub"}
                  </Button>
                  <span className="self-center text-xs text-muted-foreground">
                    GitHub access is used only for this workspace.
                  </span>
                </div>
                {githubStatusError ? <Notice tone="danger">{githubStatusError}</Notice> : null}
                <form onSubmit={onScan} className="mt-5 grid gap-3 sm:grid-cols-[1fr_1.2fr_auto]">
                  <Input
                    aria-label="Repository owner"
                    placeholder="Owner"
                    value={owner}
                    onChange={(e) => setOwner(e.target.value)}
                    required
                  />
                  <Input
                    aria-label="Repository name"
                    placeholder="Repository name"
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    required
                  />
                  <Button type="submit" disabled={scanBusy || !githubConnected}>
                    {scanBusy ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <RefreshCw className="size-4" />
                    )}{" "}
                    Scan repository
                  </Button>
                </form>
                {scanError ? <Notice tone="danger">{scanError}</Notice> : null}
                {agentHint ? (
                  <Notice tone="warn">
                    <strong>Connect your local agent to see available tools</strong>
                    <a
                      className="ml-2 underline underline-offset-4"
                      href={agentHint.startsWith("http") ? agentHint : "#local-agent"}
                    >
                      Setup instructions
                    </a>
                  </Notice>
                ) : null}
                {servers.length ? (
                  <div className="mt-5 border-t border-border pt-4">
                    <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
                      Servers found
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {servers.map((server, index) => (
                        <Badge key={index} variant="outline">
                          {typeof server === "string"
                            ? server
                            : ((server as { name?: string })?.name ?? `Server ${index + 1}`)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
              </section>
              <section id="local-agent" className="border border-border bg-card p-5">
                <SectionHeading
                  icon={Radio}
                  title="Local Agent Status"
                  description="Your local agent keeps tasks close to your development environment."
                />
                <div className="mt-7 flex items-center gap-4">
                  <span
                    className={`flex size-12 items-center justify-center rounded-full border ${agentState === "connected" ? "border-success/40 bg-success/10 text-success" : agentState === "connecting" ? "border-warn/50 bg-warn/10 text-warn" : "border-destructive/40 bg-destructive/10 text-destructive"}`}
                  >
                    {agentState === "connected" ? (
                      <Wifi className="size-5" />
                    ) : (
                      <WifiOff className="size-5" />
                    )}
                  </span>
                  <div>
                    <p className="font-medium text-foreground">{statusLabel}</p>
                    <p className="text-sm text-muted-foreground">
                      {latestEvent ? "Receiving live events" : "Waiting for the local agent"}
                    </p>
                  </div>
                </div>
                <div className="mt-6 border-t border-border pt-4 text-xs text-muted-foreground">
                  {eventCount} event{eventCount === 1 ? "" : "s"} received this session
                </div>
              </section>
            </div>
            <section className="mt-5 border border-border bg-card p-5">
              <SectionHeading
                icon={ShieldCheck}
                title="Discover and approve actions"
                description="Start with a local server or replace the results with a GitHub scan."
              />
              <form onSubmit={onDiscover} className="mt-5 grid gap-3 md:grid-cols-[180px_1fr_auto]">
                <Input
                  aria-label="Command"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="python"
                />
                <Input
                  aria-label="Arguments"
                  value={args}
                  onChange={(e) => setArgs(e.target.value)}
                  placeholder="-m, src.mcp_servers.db_server (SQLite) or src.mcp_servers.git_server <repo-path>"
                />
                <Button type="submit" disabled={loading || !command.trim()}>
                  {loading ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <RefreshCw className="size-4" />
                  )}{" "}
                  Discover tools
                </Button>
              </form>
              {loading ? (
                <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Connecting to server and listing tools...
                </p>
              ) : null}
              {error ? <Notice tone="danger">{error}</Notice> : null}
              <p className="mt-3 text-xs text-muted-foreground">
                SQLite creates its database file automatically. For a quick start, use command{" "}
                <code>python</code> and arguments <code>-m, src.mcp_servers.db_server</code>; the
                file is <code>backend/saga_demo.sqlite3</code>.
              </p>
              {tools !== null ? (
                <ToolsTable tools={tools} rows={rows} setRow={setRow} onApprove={onApprove} />
              ) : (
                <p className="mt-8 text-center text-sm text-muted-foreground">
                  Scan a repository or discover a server to review its available actions.
                </p>
              )}
            </section>
            <TaskRunner
              task={task}
              setTask={setTask}
              taskBusy={taskBusy}
              taskError={taskError}
              taskNeedsAgent={taskNeedsAgent}
              onRun={onRun}
              setupCommand={setupCommand}
            />
            <RollbackDemoPanel
              repoPath={demoRepoPath}
              setRepoPath={setDemoRepoPath}
              busy={taskBusy}
              error={demoError}
              onRun={onRunRollbackDemo}
              agentConnected={agentState === "connected"}
              backendSetupCommand={backendSetupCommand}
              setupCommand={setupCommand}
            />
            <RunSummary summary={sagaSummary} />
            <ExecutionGraph events={events} />
            <EventStream events={events} />
          </>
        ) : (
          <RegistryPanel approved={approved} onRemove={onRemove} />
        )}
      </div>
    </main>
  );
}

function AuthWall(props: {
  email: string;
  setEmail: (value: string) => void;
  password: string;
  setPassword: (value: string) => void;
  authMode: "signin" | "signup";
  setAuthMode: (value: "signin" | "signup") => void;
  authBusy: boolean;
  authError: string | null;
  onAuth: (event: React.FormEvent) => void;
  onGoogle: () => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-5 py-12">
      <div className="w-full max-w-md">
        <Link to="/" className="font-mono text-sm font-semibold tracking-[0.16em] text-foreground">
          SAGA<span className="text-warn">·</span>MCP
        </Link>
        <section className="mt-8 border border-border bg-card p-6 sm:p-8">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Private workspace
          </p>
          <h1 className="mt-3 text-2xl font-semibold text-foreground">
            Bring your team into the loop.
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Sign in to connect repositories, approve actions, and monitor agent work.
          </p>
          <Button type="button" variant="outline" className="mt-6 w-full" onClick={props.onGoogle}>
            <span className="font-semibold">G</span> Continue with Google
          </Button>
          <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            or continue with email
            <span className="h-px flex-1 bg-border" />
          </div>
          <form onSubmit={props.onAuth} className="space-y-3">
            <Input
              type="email"
              placeholder="you@company.com"
              value={props.email}
              onChange={(e) => props.setEmail(e.target.value)}
              required
            />
            <Input
              type="password"
              placeholder="Password"
              value={props.password}
              onChange={(e) => props.setPassword(e.target.value)}
              required
              minLength={6}
            />
            <Button
              type="submit"
              className="w-full bg-warn text-warn-foreground hover:bg-warn/90"
              disabled={props.authBusy}
            >
              {props.authBusy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : props.authMode === "signin" ? (
                "Sign in"
              ) : (
                "Create account"
              )}
            </Button>
          </form>
          {props.authError ? <Notice tone="danger">{props.authError}</Notice> : null}
          <button
            type="button"
            className="mt-5 w-full text-center text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
            onClick={() => props.setAuthMode(props.authMode === "signin" ? "signup" : "signin")}
          >
            {props.authMode === "signin"
              ? "Need an account? Create one"
              : "Already have an account? Sign in"}
          </button>
        </section>
      </div>
    </main>
  );
}
function AgentStatus({
  state,
  label,
}: {
  state: "offline" | "connecting" | "connected";
  label: string;
}) {
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        className={`size-2 rounded-full ${state === "connected" ? "bg-success" : state === "connecting" ? "bg-warn saga-pulse" : "bg-destructive"}`}
      />
      {label}
    </span>
  );
}
function SectionHeading({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Github;
  title: string;
  description: string;
}) {
  return (
    <div className="flex gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center border border-border bg-surface-2 text-warn">
        <Icon className="size-4" />
      </span>
      <div>
        <h2 className="font-semibold text-foreground">{title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}
function Notice({ children, tone }: { children: React.ReactNode; tone: "danger" | "warn" }) {
  return (
    <div
      className={`mt-4 flex flex-wrap items-center gap-2 border px-3 py-2 text-sm ${tone === "danger" ? "border-destructive/40 bg-destructive/10 text-destructive" : "border-warn/40 bg-warn/10 text-warn"}`}
    >
      {tone === "danger" ? <CircleX className="size-4" /> : <AlertCircle className="size-4" />}
      {children}
    </div>
  );
}
function ToolsTable({
  tools,
  rows,
  setRow,
  onApprove,
}: {
  tools: DiscoveredTool[];
  rows: Record<string, RowState>;
  setRow: (name: string, patch: Partial<RowState>) => void;
  onApprove: (tool: DiscoveredTool) => void;
}) {
  return (
    <div className="mt-6 overflow-x-auto border border-border">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr className="border-b border-border">
            <th className="px-4 py-3 font-normal">Tool Name</th>
            <th className="px-4 py-3 font-normal">Description</th>
            <th className="px-4 py-3 font-normal">Suggested Pairing</th>
            <th className="px-4 py-3 font-normal">Category</th>
            <th className="px-4 py-3 font-normal">Retry Safety</th>
            <th className="px-4 py-3 font-normal">Confidence</th>
            <th className="px-4 py-3 font-normal">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {tools.map((tool) => {
            const row = rows[tool.name];
            if (!row) return null;
            const low = (tool.confidence ?? "").toLowerCase() === "low";
            return (
              <tr key={tool.name} className="align-top">
                <td className="px-4 py-3 font-medium text-foreground">{tool.name}</td>
                <td className="max-w-[280px] px-4 py-3 text-muted-foreground">
                  {tool.description}
                </td>
                <td className="px-4 py-3">
                  <select
                    aria-label={`Pairing for ${tool.name}`}
                    className="w-44 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-sm text-foreground disabled:opacity-50"
                    value={row.pairing}
                    disabled={row.category === "pivot" || row.approved}
                    onChange={(e) => setRow(tool.name, { pairing: e.target.value })}
                  >
                    <option value="">No undo selected</option>
                    {tools
                      .filter((other) => other.name !== tool.name)
                      .map((other) => (
                        <option key={other.name} value={other.name}>
                          {other.name}
                        </option>
                      ))}
                  </select>
                </td>
                <td className="px-4 py-3">
                  <select
                    aria-label={`Category for ${tool.name}`}
                    className="w-44 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-sm text-foreground disabled:opacity-50"
                    value={row.category}
                    disabled={row.approved}
                    onChange={(e) => setRow(tool.name, { category: e.target.value })}
                  >
                    {CATEGORIES.map((category) => (
                      <option key={category.value} value={category.value}>
                        {category.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-3">
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      aria-label={`Compensation for ${tool.name} is safe to retry`}
                      checked={row.compensationIdempotent}
                      disabled={row.category === "pivot" || row.approved}
                      onChange={(event) =>
                        setRow(tool.name, { compensationIdempotent: event.target.checked })
                      }
                    />
                    Safe to retry
                  </label>
                </td>
                <td className="px-4 py-3">
                  <Badge
                    variant="outline"
                    className={
                      low ? "border-warn/50 bg-warn/10 text-warn" : "text-muted-foreground"
                    }
                  >
                    {low ? "needs review" : "high confidence"}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  {row.approved ? (
                    <Badge className="border-success/50 bg-success/10 text-success">
                      <Check className="mr-1 size-3" />
                      Approved
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={row.busy}
                      onClick={() => void onApprove(tool)}
                    >
                      {row.busy ? "Approving..." : "Approve"}
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
function TaskRunner({
  task,
  setTask,
  taskBusy,
  taskError,
  taskNeedsAgent,
  onRun,
  setupCommand,
}: {
  task: string;
  setTask: (value: string) => void;
  taskBusy: boolean;
  taskError: string | null;
  taskNeedsAgent: boolean;
  onRun: (event: React.FormEvent) => void;
  setupCommand: string;
}) {
  const copy = () => void navigator.clipboard?.writeText(setupCommand);
  return (
    <section className="mt-5 border border-border bg-card p-5">
      <SectionHeading
        icon={Send}
        title="Run a task"
        description="Describe the work and watch the local agent carry it out."
      />
      <form onSubmit={onRun} className="mt-5 flex flex-col gap-3 sm:flex-row">
        <Textarea
          aria-label="Task description"
          className="min-h-12 resize-none"
          placeholder="e.g. Seed the test database and deploy the staging container"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          required
        />
        <Button type="submit" disabled={taskBusy || !task.trim()}>
          {taskBusy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />} Run
        </Button>
      </form>
      {taskError ? (
        <Notice tone="danger">
          <span>{taskError}</span>
          {taskNeedsAgent ? (
            <>
              <code className="rounded bg-background/60 px-2 py-1 text-xs">{setupCommand}</code>
              <Button type="button" variant="ghost" size="sm" onClick={copy}>
                <Copy className="size-3" />
                Copy setup command
              </Button>
            </>
          ) : null}
        </Notice>
      ) : null}
    </section>
  );
}
function RollbackDemoPanel({
  repoPath,
  setRepoPath,
  busy,
  error,
  onRun,
  agentConnected,
  backendSetupCommand,
  setupCommand,
}: {
  repoPath: string;
  setRepoPath: (value: string) => void;
  busy: boolean;
  error: string | null;
  onRun: (event: React.FormEvent) => void;
  agentConnected: boolean;
  backendSetupCommand: string;
  setupCommand: string;
}) {
  const copyBackend = () => void navigator.clipboard?.writeText(backendSetupCommand);
  const copyAgent = () => void navigator.clipboard?.writeText(setupCommand);
  return (
    <section className="mt-5 border border-border bg-card p-5">
      <SectionHeading
        icon={GitBranch}
        title="Execute + fail + compensate"
        description="A real local Git branch, a deliberate SQLite failure, and the approved inverse action."
      />
      <form onSubmit={onRun} className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]">
        <Input
          aria-label="Local Git repository path"
          placeholder="Path to a local Git repository"
          value={repoPath}
          onChange={(event) => setRepoPath(event.target.value)}
          required
        />
        <Button type="submit" disabled={busy || !agentConnected || !repoPath.trim()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <GitBranch className="size-4" />}{" "}
          Run rollback demo
        </Button>
      </form>
      <p className="mt-3 text-xs text-muted-foreground">
        Requires approved pairings: create_branch → delete_branch and seed_database → wipe_database.
      </p>
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {!agentConnected ? (
        <div className="mt-4 border-t border-border pt-4">
          <p className="text-sm font-medium text-foreground">Local agent is offline</p>
          <p className="mt-3 text-xs font-medium text-foreground">Backend terminal</p>
          <pre className="mt-2 overflow-x-auto border border-border bg-background p-3 text-xs text-muted-foreground">
            {backendSetupCommand}
          </pre>
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={copyBackend}>
            <Copy className="size-3" />
            Copy backend command
          </Button>
          <p className="mt-4 text-xs font-medium text-foreground">
            Second terminal for the local agent
          </p>
          <pre className="mt-2 overflow-x-auto border border-border bg-background p-3 text-xs text-muted-foreground">
            {setupCommand}
          </pre>
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={copyAgent}>
            <Copy className="size-3" />
            Copy agent command
          </Button>
        </div>
      ) : null}
    </section>
  );
}
function RunSummary({ summary }: { summary: SagaSummary | null }) {
  if (!summary?.saga_id) return null;
  const steps = summary.steps ?? [];
  const metrics = [
    ["Forward steps", String(steps.length)],
    ["Compensated", String(steps.filter((step) => step.status === "compensated").length)],
    [
      "Failures",
      String(
        steps.filter((step) => step.status === "failed" || step.status === "compensation_failed")
          .length,
      ),
    ],
    ["DLQ", String(steps.filter((step) => step.status === "compensation_failed").length)],
  ];
  return (
    <section className="mt-5 border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            Saga status
          </p>
          <h3 className="mt-1 text-lg font-semibold text-foreground">
            {(summary.status ?? "unknown").replaceAll("_", " ").toUpperCase()}
          </h3>
        </div>
        <code className="text-xs text-muted-foreground">{summary.saga_id}</code>
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label} className="border border-border bg-surface-2 px-3 py-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 font-mono text-xl text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      {steps.length ? (
        <ol className="mt-4 space-y-2 border-t border-border pt-4">
          {steps.map((step, index) => (
            <li
              key={`${step.tool_name}-${index}`}
              className="flex items-center justify-between gap-3 text-sm"
            >
              <span className="text-foreground">
                <span className="mr-2 font-mono text-xs text-muted-foreground">{index + 1}.</span>
                {step.tool_name}
              </span>
              <Badge variant="outline">{step.status.replaceAll("_", " ")}</Badge>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
function ExecutionGraph({ events }: { events: StreamEvent[] }) {
  const steps = [
    ...new Map(
      events.map((event, index) => [
        `${String(event.step_id ?? event.tool_name ?? index)}:${String(event.event_type ?? event.type ?? "EVENT")}`,
        event,
      ]),
    ).values(),
  ]
    .sort((left, right) => {
      const leftTime = Number(left.timestamp ?? 0);
      const rightTime = Number(right.timestamp ?? 0);
      return leftTime - rightTime;
    })
    .slice(-5);
  const nodes: Node[] = steps.map((event, index) => {
    const kind = String(event.event_type ?? event.type ?? "EVENT");
    const failed = kind === "FAULT_DETECTED" || kind === "DEAD_LETTER";
    const compensated = kind === "COMPENSATED";
    const color = failed
      ? "hsl(var(--destructive))"
      : compensated
        ? "hsl(var(--muted-foreground))"
        : "hsl(var(--success))";
    return {
      id: `${String(event.step_id ?? event.tool_name ?? index)}:${kind}`,
      position: { x: index * 220, y: 34 },
      data: {
        label: (
          <div className="min-w-36">
            <p className="font-mono text-[10px] uppercase">{kind.replaceAll("_", " ")}</p>
            <p className="mt-2 text-sm font-semibold">
              {String(event.tool_name ?? event.step_id ?? "Agent update")}
            </p>
            <p className="mt-1 text-xs opacity-75">{String(event.status ?? "live")}</p>
            {event["reason"] ? (
              <p className="mt-1 max-w-44 text-xs opacity-75">{String(event["reason"])}</p>
            ) : null}
          </div>
        ),
      },
      style: {
        width: 190,
        color: "hsl(var(--foreground))",
        borderColor: color,
        background: "hsl(var(--card))",
        borderRadius: 3,
      },
    };
  });
  const edges: Edge[] = nodes.slice(1).flatMap((node, index) => {
    const source = nodes[index];
    return source
      ? [
          {
            id: `transition-${index}`,
            source: source.id,
            target: node.id,
            type: "smoothstep",
            markerEnd: { type: MarkerType.ArrowClosed, color: "hsl(var(--muted-foreground))" },
          },
        ]
      : [];
  });
  return (
    <section className="mt-5 border border-border bg-card p-5">
      <div className="flex items-center justify-between gap-3">
        <SectionHeading
          icon={Activity}
          title="Execution path"
          description="Live forward, fault, and compensation transitions."
        />
        <span className="font-mono text-xs text-muted-foreground">{steps.length} transitions</span>
      </div>
      {steps.length ? (
        <div className="mt-4 h-64 border border-border bg-background">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="hsl(var(--border))" gap={22} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      ) : (
        <p className="mt-6 text-sm text-muted-foreground">
          Run a task to populate the execution path with live saga events.
        </p>
      )}
    </section>
  );
}
function EventStream({ events }: { events: StreamEvent[] }) {
  return (
    <section className="mt-5 border border-border bg-card p-5">
      <div className="flex items-center justify-between gap-3">
        <SectionHeading
          icon={Radio}
          title="Live event stream"
          description="A running record of every forward action, fault, and recovery."
        />
        <span className="font-mono text-xs text-muted-foreground">{events.length} events</span>
      </div>
      {events.length ? (
        <div className="mt-5 space-y-2">
          {events.map((event, index) => {
            const kind = String(event.event_type ?? event.type ?? "EVENT");
            return (
              <div
                key={index}
                className={`flex flex-wrap items-center gap-3 border px-3 py-3 text-sm ${EVENT_STYLES[kind] ?? "border-border bg-surface-2 text-muted-foreground"}`}
              >
                <span className="font-mono text-[10px] uppercase tracking-[0.12em]">{kind}</span>
                <span className="text-foreground">
                  {String(event.tool_name ?? event.step_id ?? "Agent update")}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  {String(event.timestamp ?? event.status ?? "live")}
                </span>
                {kind === "DEAD_LETTER" ? (
                  <strong className="basis-full text-destructive">
                    Review required before continuing.
                  </strong>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="mt-6 text-sm text-muted-foreground">
          Events will appear here when the local agent connects.
        </p>
      )}
    </section>
  );
}
function RegistryPanel({
  approved,
  onRemove,
}: {
  approved: ApprovedEntry[];
  onRemove: (name: string) => void;
}) {
  return (
    <section className="border border-border bg-card p-5">
      <SectionHeading
        icon={ShieldCheck}
        title="Approved safety registry"
        description="These are the actions currently cleared for agent use."
      />
      {approved.length ? (
        <ul className="mt-6 divide-y divide-border border border-border">
          {approved.map((entry) => (
            <li
              key={entry.tool_name}
              className="flex flex-wrap items-center gap-3 px-4 py-4 text-sm"
            >
              <span className="font-medium text-foreground">{entry.tool_name}</span>
              <span className="text-muted-foreground">
                →{" "}
                {normalizeCategory(entry.category) === "pivot"
                  ? "Irreversible — no undo"
                  : entry.compensating_tool || "no undo selected"}
              </span>
              <Button
                className="ml-auto"
                variant="ghost"
                size="sm"
                onClick={() => void onRemove(entry.tool_name)}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-8 text-center text-sm text-muted-foreground">No approved actions yet.</p>
      )}
    </section>
  );
}
