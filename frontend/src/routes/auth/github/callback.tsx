import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Loader2, CircleCheck, CircleX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { supabase } from "@/integrations/supabase/client";
import { connectGitHub } from "@/lib/studio/connected-api";

export const Route = createFileRoute("/auth/github/callback")({
  head: () => ({
    meta: [
      { title: "Saga-MCP — GitHub connected" },
      { name: "description", content: "Finish connecting your GitHub account to Saga-MCP." },
      { property: "og:title", content: "Saga-MCP — GitHub connected" },
      { property: "og:description", content: "Finish connecting your GitHub account to Saga-MCP." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: GitHubCallback,
});

function GitHubCallback() {
  const [state, setState] = useState<"working" | "done" | "error">("working");
  const [message, setMessage] = useState("Finishing your GitHub connection…");

  useEffect(() => {
    const finish = async () => {
      const params = new URLSearchParams(window.location.search);
      const providerError = params.get("error_description") ?? params.get("error");
      if (providerError) {
        setState("error");
        setMessage(providerError);
        return;
      }
      const code = params.get("code");
      const state = params.get("state");
      if (!code) {
        setState("error");
        setMessage("GitHub did not return a connection code.");
        return;
      }
      if (!state) {
        setState("error");
        setMessage("GitHub did not return an OAuth state value.");
        return;
      }
      let token = (await supabase.auth.getSession()).data.session?.access_token;
      if (!token) {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
        token = (await supabase.auth.getSession()).data.session?.access_token;
      }
      if (!token) {
        setState("error");
        setMessage("Your sign-in session expired. Return to the studio and try again.");
        return;
      }
      try {
        await connectGitHub(code, state, token);
        setState("done");
        setMessage("GitHub is connected. You can now scan a repository.");
      } catch (error) {
        setState("error");
        setMessage(error instanceof Error ? error.message : "GitHub could not be connected.");
      }
    };
    void finish();
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-5">
      <section className="w-full max-w-md border border-border bg-card p-8 text-center">
        {state === "working" ? (
          <Loader2 className="mx-auto size-8 animate-spin text-warn" />
        ) : state === "done" ? (
          <CircleCheck className="mx-auto size-8 text-success" />
        ) : (
          <CircleX className="mx-auto size-8 text-destructive" />
        )}
        <h1 className="mt-5 text-xl font-semibold text-foreground">
          {state === "done"
            ? "GitHub connected"
            : state === "error"
              ? "Connection needs attention"
              : "Connecting GitHub"}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{message}</p>
        {state !== "working" ? (
          <Button asChild className="mt-6">
            <Link to="/studio">Return to Studio</Link>
          </Button>
        ) : null}
      </section>
    </main>
  );
}
