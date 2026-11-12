import { createServerFn } from "@tanstack/react-start";
import { getRequest } from "@tanstack/react-start/server";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";

const GATEWAY_BASE_URL = "https://connector-gateway.lovable.dev";
const GITHUB_SCOPES = ["read:user", "repo"];

function requireSecret(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error("GitHub account connection is not configured yet.");
  return value;
}

export const startGitHubAccountConnect = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }) => {
    const request = getRequest();
    if (!request) throw new Error("GitHub connection must start from the app.");
    const clientAPIKey = requireSecret("GITHUB_APP_USER_CONNECTOR_CLIENT_API_KEY");
    const { authorizeAppUserOAuth } = await import("@/integrations/lovable/appUserConnector");
    const { authorizationUrl } = await authorizeAppUserOAuth({
      gatewayBaseUrl: GATEWAY_BASE_URL,
      connectorId: "github",
      appUserId: context.userId,
      clientAPIKey,
      returnUrl: new URL("/api/public/oauth/github/return", new URL(request.url).origin).toString(),
      credentialsConfiguration: { scopes: GITHUB_SCOPES },
    });
    return { authorizationUrl };
  });

export const completeGitHubAccountConnect = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: { code: string }) => input)
  .handler(async ({ data, context }) => {
    if (!data.code || data.code.length > 4096)
      throw new Error("GitHub returned an invalid connection code.");
    const { exchangeAppUserOAuthCode } = await import("@/integrations/lovable/appUserConnector");
    const { connectionAPIKey } = await exchangeAppUserOAuthCode(GATEWAY_BASE_URL, data.code);
    const { saveGitHubConnection } = await import("@/lib/github-connection.server");
    await saveGitHubConnection(
      context.userId,
      connectionAPIKey,
      requireSecret("APP_USER_CONNECTION_KEY_SECRET"),
    );
    return { connected: true };
  });

export const getGitHubAccountStatus = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }) => {
    const { getGitHubConnection } = await import("@/lib/github-connection.server");
    const key = await getGitHubConnection(
      context.userId,
      requireSecret("APP_USER_CONNECTION_KEY_SECRET"),
    );
    return { connected: key !== null };
  });

export const listGitHubAccountRepositories = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }) => {
    const { getGitHubConnection } = await import("@/lib/github-connection.server");
    const connectionAPIKey = await getGitHubConnection(
      context.userId,
      requireSecret("APP_USER_CONNECTION_KEY_SECRET"),
    );
    if (!connectionAPIKey)
      return { connected: false as const, reconnectRequired: false, repositories: [] };
    const { appUserReconnectRequired, callAsAppUser } =
      await import("@/integrations/lovable/appUserConnector");
    const response = await callAsAppUser({
      gatewayBaseUrl: GATEWAY_BASE_URL,
      connectionAPIKey,
      connectorId: "github",
      path: "/user/repos?per_page=100&sort=updated",
      requiredScopes: GITHUB_SCOPES,
    });
    if (await appUserReconnectRequired(response))
      return { connected: false as const, reconnectRequired: true, repositories: [] };
    if (!response.ok) {
      const detail = await response.text();
      console.error(`GitHub repository list failed [${response.status}]: ${detail}`);
      throw new Error(`GitHub could not list repositories (${response.status}).`);
    }
    const result: unknown = await response.json();
    if (!Array.isArray(result)) throw new Error("GitHub returned an invalid repository list.");
    const repositories = result.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const repo = item as { name?: unknown; full_name?: unknown; private?: unknown };
      return typeof repo.name === "string" && typeof repo.full_name === "string"
        ? [{ name: repo.name, full_name: repo.full_name, private: Boolean(repo.private) }]
        : [];
    });
    return { connected: true as const, reconnectRequired: false, repositories };
  });
