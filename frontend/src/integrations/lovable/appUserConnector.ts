/** Server-only helpers for per-user GitHub OAuth and provider requests. */
function requireApiKey(): string {
  const key = process.env["LOVABLE_API_KEY"];
  if (!key) throw new Error("GitHub account connection is not available yet.");
  return key;
}

export async function authorizeAppUserOAuth(params: {
  gatewayBaseUrl: string;
  connectorId: string;
  appUserId: string;
  clientAPIKey: string;
  returnUrl: string;
  credentialsConfiguration?: Record<string, unknown>;
}) {
  const response = await fetch(`${params.gatewayBaseUrl}/api/v1/app-users/oauth2/authorize`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${requireApiKey()}`,
      "Content-Type": "application/json",
      "X-Client-Api-Key": params.clientAPIKey,
    },
    body: JSON.stringify({
      connector_id: params.connectorId,
      app_user_id: params.appUserId,
      return_url: params.returnUrl,
      credentials_configuration: params.credentialsConfiguration,
    }),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`GitHub authorization could not start (${response.status}).`);
  let body: { authorization_url?: string };
  try {
    body = JSON.parse(text) as { authorization_url?: string };
  } catch {
    throw new Error("GitHub authorization returned an invalid response.");
  }
  if (!body.authorization_url)
    throw new Error("GitHub authorization did not return a sign-in link.");
  return { authorizationUrl: body.authorization_url };
}

export async function exchangeAppUserOAuthCode(gatewayBaseUrl: string, code: string) {
  const response = await fetch(`${gatewayBaseUrl}/api/v1/app-users/oauth2/exchange`, {
    method: "POST",
    headers: { Authorization: `Bearer ${requireApiKey()}`, "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`GitHub connection could not be saved (${response.status}).`);
  let body: { api_key?: string; connector_id?: string };
  try {
    body = JSON.parse(text) as { api_key?: string; connector_id?: string };
  } catch {
    throw new Error("GitHub connection returned an invalid response.");
  }
  if (!body.api_key || body.connector_id !== "github")
    throw new Error("GitHub connection could not be verified.");
  return { connectionAPIKey: body.api_key, connectorId: body.connector_id };
}

export async function callAsAppUser(params: {
  gatewayBaseUrl: string;
  connectionAPIKey: string;
  connectorId: string;
  path: string;
  requiredScopes?: string[];
}) {
  const headers = new Headers({
    Authorization: `Bearer ${requireApiKey()}`,
    "X-Connection-Api-Key": params.connectionAPIKey,
    Accept: "application/vnd.github+json",
  });
  if (params.requiredScopes?.length)
    headers.set("X-Lovable-Required-Scopes", params.requiredScopes.join(" "));
  return fetch(`${params.gatewayBaseUrl}/${params.connectorId}${params.path}`, { headers });
}

export async function appUserReconnectRequired(response: Response) {
  if (response.status !== 401) return false;
  const body = (await response
    .clone()
    .json()
    .catch(() => null)) as { type?: unknown } | null;
  return typeof body?.type === "string" && body.type.startsWith("credential_");
}
