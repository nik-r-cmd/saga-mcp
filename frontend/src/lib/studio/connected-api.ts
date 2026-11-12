import { API_BASE, type ApprovedEntry, type DiscoveredTool } from "@/lib/studio/api";

export const BACKEND_URL = API_BASE;

async function readResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail =
      typeof body === "string"
        ? body
        : ((body as { detail?: unknown; message?: unknown } | null)?.detail ??
          (body as { message?: unknown } | null)?.message);
    const error = new Error(
      typeof detail === "string" && detail.trim() ? detail : `Request failed (${response.status})`,
    );
    Object.assign(error, { status: response.status });
    throw error;
  }
  return body as T;
}

export async function beginGitHubConnect(token: string) {
  const response = await fetch(`${BACKEND_URL}/auth/github/authorize`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({}),
  });
  return readResponse<{ authorization_url: string }>(response);
}

export async function connectGitHub(code: string, state: string, token: string) {
  const response = await fetch(`${BACKEND_URL}/auth/github/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ code, state }),
  });
  return readResponse<{ ok?: boolean }>(response);
}

export async function getGitHubConnectionStatus(token: string) {
  const response = await fetch(`${BACKEND_URL}/auth/github/status`, {
    headers: { Authorization: "Bearer " + token },
  });
  return readResponse<{ connected: boolean }>(response);
}

export interface ScanResult {
  discovery?: string;
  servers_found?: unknown[];
  tools?: DiscoveredTool[];
  setup_url?: string;
  setup_instructions_url?: string;
}

export async function scanGitHubRepo(owner: string, repo: string, token: string) {
  const response = await fetch(`${BACKEND_URL}/scan-github-repo`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ owner, repo }),
  });
  return readResponse<ScanResult>(response);
}

export interface McpServerRegistration {
  command: string;
  args: string[];
  tool_names: string[];
}

export interface TaskResult {
  type: "task_result";
  request_id?: string;
  saga_id?: string;
  status: string;
  error?: string;
  steps?: Array<{
    tool_name: string;
    status: string;
    failure_reason?: string;
    failure_detail?: string;
  }>;
}

async function waitForTaskResult(requestId: string, token: string): Promise<TaskResult> {
  const deadline = Date.now() + 600_000;
  while (Date.now() < deadline) {
    const response = await fetch(`${BACKEND_URL}/tasks/${encodeURIComponent(requestId)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const task = await readResponse<{ status: string; result: TaskResult | null }>(response);
    if (task.result) return task.result;
    if (task.status === "failed")
      throw new Error("The local agent disconnected before completing the task.");
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  throw new Error("Timed out after 10 minutes waiting for the local agent result.");
}

export async function runSagaTask(
  input: {
    task_description: string;
    mcp_command: string;
    mcp_args: string[];
    mcp_servers?: McpServerRegistration[];
  },
  token: string,
): Promise<TaskResult> {
  const registryResponse = await fetch(`${BACKEND_URL}/registry`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const registry = await readResponse<{ approved: ApprovedEntry[] }>(registryResponse);
  const grouped = new Map<string, McpServerRegistration>();
  for (const entry of registry.approved) {
    if (!entry.server_command) continue;
    const args = entry.server_args ?? [];
    const key = JSON.stringify([entry.server_command, args]);
    const server = grouped.get(key) ?? { command: entry.server_command, args, tool_names: [] };
    if (!server.tool_names.includes(entry.tool_name)) server.tool_names.push(entry.tool_name);
    if (entry.compensating_tool && !server.tool_names.includes(entry.compensating_tool)) {
      server.tool_names.push(entry.compensating_tool);
    }
    grouped.set(key, server);
  }

  const response = await fetch(`${BACKEND_URL}/run-task`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ ...input, mcp_servers: [...grouped.values()] }),
  });
  const dispatched = await readResponse<{ request_id: string }>(response);
  const result = await waitForTaskResult(dispatched.request_id, token);
  window.dispatchEvent(new CustomEvent("saga-task-result", { detail: result }));
  return result;
}

export async function getAgentStatus(token: string) {
  const response = await fetch(`${BACKEND_URL}/agent/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return readResponse<{ connected: boolean }>(response);
}

export async function runRollbackDemo(repoPath: string, token: string): Promise<TaskResult> {
  const response = await fetch(`${BACKEND_URL}/run-rollback-demo`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ repo_path: repoPath }),
  });
  const dispatched = await readResponse<{ request_id: string }>(response);
  const result = await waitForTaskResult(dispatched.request_id, token);
  window.dispatchEvent(new CustomEvent("saga-task-result", { detail: result }));
  return result;
}

export async function getWebSocketTicket(token: string) {
  const response = await fetch(`${BACKEND_URL}/ws-ticket`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  return readResponse<{ ticket: string }>(response);
}

export function websocketUrl(ticket: string) {
  const url = new URL(BACKEND_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/saga-events";
  url.search = new URLSearchParams({ ticket }).toString();
  return url.toString();
}

export function toolsFromScan(result: ScanResult): DiscoveredTool[] {
  if (Array.isArray(result.tools)) return result.tools;
  const found: DiscoveredTool[] = [];
  for (const server of result.servers_found ?? []) {
    if (!server || typeof server !== "object") continue;
    const record = server as { tools?: unknown[]; name?: string; description?: string };
    if (Array.isArray(record.tools)) {
      for (const item of record.tools) {
        if (!item || typeof item !== "object") continue;
        const tool = item as Partial<DiscoveredTool>;
        if (typeof tool.name === "string")
          found.push({
            name: tool.name,
            description: tool.description ?? record.name ?? "Discovered from GitHub",
            input_schema: tool.input_schema,
            suggested_category: tool.suggested_category ?? "compensable",
            suggested_compensating_tool: tool.suggested_compensating_tool ?? null,
            confidence: tool.confidence ?? "high",
            already_approved: Boolean(tool.already_approved),
          });
      }
    } else if (record.name) {
      found.push({
        name: record.name,
        description: record.description ?? "Discovered from GitHub",
        suggested_category: "compensable",
        suggested_compensating_tool: null,
        confidence: "high",
        already_approved: false,
      });
    }
  }
  return found;
}
