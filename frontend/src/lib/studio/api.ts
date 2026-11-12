import { supabase } from "@/integrations/supabase/client";

export const API_BASE = (import.meta.env["VITE_BACKEND_URL"] || "http://localhost:8000").replace(
  /\/+$/,
  "",
);
export const WS_BASE = API_BASE.replace(/^http/, "ws");

export interface DiscoveredTool {
  name: string;
  description: string;
  input_schema?: { properties?: Record<string, unknown>; required?: string[] } | undefined;
  suggested_category: string;
  suggested_compensating_tool: string | null;
  confidence: string;
  already_approved: boolean;
}

export interface ApprovedEntry {
  tool_name: string;
  category: string;
  compensating_tool: string | null;
  server_command: string | null;
  server_args: string[];
  compensation_arg_mapping: Record<string, string> | null;
  compensation_result_arg_mapping: Record<string, string> | null;
  compensation_idempotent: boolean;
  tool_description: string;
  input_schema: { properties?: Record<string, unknown>; required?: string[] };
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    throw new Error("Not signed in");
  }

  const headers = new Headers(init.headers ?? {});
  headers.set("Content-Type", "application/json");
  headers.set("Authorization", `Bearer ${session.access_token}`);

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });

  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }

  if (!res.ok) {
    const detail =
      typeof body === "string"
        ? body
        : ((body as { detail?: unknown; message?: unknown } | null)?.detail ??
          (body as { message?: unknown } | null)?.message);
    throw new Error(
      typeof detail === "string" && detail.trim()
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `Request failed (${res.status})`,
    );
  }

  return (body ?? null) as T;
}

export async function discoverTools(command: string, args: string[]) {
  const dispatched = await apiFetch<{ request_id: string }>("/discover", {
    method: "POST",
    body: JSON.stringify({ command, args }),
  });
  const deadline = Date.now() + 300_000;
  while (Date.now() < deadline) {
    const task = await apiFetch<{
      status: string;
      result: { type?: string; tools_by_server?: Record<string, unknown> } | null;
    }>(`/tasks/${encodeURIComponent(dispatched.request_id)}`);
    if (task.status === "failed")
      throw new Error("The local agent disconnected during tool discovery.");
    if (task.result) {
      const tools = task.result.tools_by_server?.["manual-discovery"];
      if (Array.isArray(tools)) return { tools: tools as DiscoveredTool[] };
      if (tools && typeof tools === "object" && "error" in tools) {
        throw new Error(String((tools as { error: unknown }).error));
      }
      throw new Error("The local agent returned an invalid discovery result.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  throw new Error("Timed out waiting for local tool discovery.");
}

export async function getRegistry() {
  return apiFetch<{ approved: ApprovedEntry[] }>("/registry");
}

export async function approveTool(input: {
  tool_name: string;
  category: string;
  compensating_tool?: string | null;
  server_command?: string | null;
  server_args?: string[];
  compensation_arg_mapping?: Record<string, string> | null;
  compensation_result_arg_mapping?: Record<string, string> | null;
  compensation_idempotent?: boolean;
  tool_description?: string;
  input_schema?: { properties?: Record<string, unknown>; required?: string[] };
}) {
  return apiFetch<unknown>("/registry/approve", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function removeTool(toolName: string) {
  return apiFetch<unknown>(`/registry/${encodeURIComponent(toolName)}`, {
    method: "DELETE",
  });
}
