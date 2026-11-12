import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/api/public/oauth/github/return")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const connectorId = url.searchParams.get("connector_id") ?? "";
        const code = url.searchParams.get("code") ?? "";
        const success = url.searchParams.get("success") === "true";
        const error = url.searchParams.get("error") ?? "GitHub authorization did not complete.";
        const result =
          connectorId === "github" && success && code
            ? { success: true, connectorId, code }
            : { success: false, connectorId, error };
        const safeJson = JSON.stringify(result).replace(/</g, "\\u003c");
        const html = `<!doctype html><html><head><meta charset="utf-8"><title>GitHub connection</title></head><body><p>${result.success ? "GitHub connected. This window can close." : "GitHub connection was not completed. Return to Saga-MCP and try again."}</p><script>
          const result = ${safeJson};
          const type = result.success ? "appUserConnectorOAuthComplete" : "appUserConnectorOAuthFailed";
          if (window.opener && window.opener !== window) window.opener.postMessage({ type, connectorId: result.connectorId, code: result.code ?? null }, window.location.origin);
          window.close();
        </script></body></html>`;
        return new Response(html, {
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
        });
      },
    },
  },
});
