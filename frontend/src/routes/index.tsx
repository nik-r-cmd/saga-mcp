import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight, CircleCheck, LockKeyhole, RotateCcw, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";

const TITLE = "Saga-MCP — Put AI agents to work with confidence";
const DESC =
  "Saga-MCP helps teams put AI agents to work without losing control of the changes they make.";

export const Route = createFileRoute("/")({
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
  component: Landing,
});

const OUTCOMES = [
  {
    icon: ShieldCheck,
    title: "Keep risky changes in check",
    body: "Review what an agent can do before it reaches your systems. Your team stays in the loop when it matters.",
  },
  {
    icon: RotateCcw,
    title: "Know how to move back",
    body: "Give every reversible action a clear way back, and call out the changes that need extra care before they run.",
  },
  {
    icon: CircleCheck,
    title: "Make approval easy to trust",
    body: "See the action, its category, and its suggested reversal together so decisions are quick and informed.",
  },
  {
    icon: LockKeyhole,
    title: "Recover without guesswork",
    body: "When a task stops partway through, completed work can be handled in reverse order instead of leaving a trail of surprises.",
  },
];

const STEPS = [
  {
    n: "1",
    t: "Connect your first server",
    d: "Tell Saga-MCP how to start it. We’ll show you what it makes available.",
  },
  {
    n: "2",
    t: "Review the important details",
    d: "Check each action, its category, and the suggested way to reverse it.",
  },
  {
    n: "3",
    t: "Approve when ready",
    d: "Approve one action at a time. Only the actions you trust make it into your registry.",
  },
];

function Landing() {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b border-border/70 bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center gap-6 px-6">
          <Link
            to="/"
            className="font-mono text-sm font-semibold tracking-[0.16em] text-foreground"
          >
            SAGA<span className="text-warn">·</span>MCP
          </Link>
          <nav className="ml-auto flex items-center gap-5 text-sm">
            <a
              href="#how-it-works"
              className="hidden text-muted-foreground transition-colors hover:text-foreground sm:block"
            >
              How it works
            </a>
            <Button asChild size="sm" className="bg-warn text-warn-foreground hover:bg-warn/90">
              <Link to="/studio">Open Studio</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main>
        <section className="relative overflow-hidden border-b border-border">
          <div className="pointer-events-none absolute inset-0 grid-backdrop opacity-[0.28]" />
          <div className="relative mx-auto w-full max-w-6xl px-6 pb-16 pt-16 sm:pb-24 sm:pt-24 md:pb-28 md:pt-32">
            <div className="max-w-3xl">
              <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
                For teams putting AI into production
              </div>
              <h1 className="font-display text-4xl font-semibold leading-[1.08] tracking-tight text-foreground sm:text-5xl md:text-7xl">
                Put AI agents to work
                <br />
                <span className="text-warn">without losing control.</span>
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted-foreground sm:text-lg">
                Saga-MCP gives your team a clear view of what an agent can change, how to reverse
                it, and when to approve it — before work reaches your systems.
              </p>
              <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                <Button
                  asChild
                  size="lg"
                  className="w-full bg-warn text-warn-foreground hover:bg-warn/90 sm:w-auto"
                >
                  <Link to="/studio">
                    Start with your first server
                    <ArrowRight className="size-4" />
                  </Link>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="w-full border-border bg-surface/40 sm:w-auto"
                >
                  <a href="#how-it-works">See the path to approval</a>
                </Button>
              </div>
            </div>

            <div className="mt-16 grid max-w-3xl gap-3 border-t border-border pt-6 sm:grid-cols-3 sm:gap-6">
              {[
                ["Before it runs", "Review every action"],
                ["Before you approve", "See its way back"],
                ["When work changes", "Stay in the loop"],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    {k}
                  </dt>
                  <dd className="mt-2 text-sm font-medium text-foreground">{v}</dd>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-b border-border bg-surface/25">
          <div className="mx-auto w-full max-w-6xl px-6 py-16 sm:py-20">
            <div className="mb-10 max-w-xl">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                What your team gets
              </p>
              <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
                Confidence before the first change.
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground sm:text-base">
                A practical layer of review and recovery for teams bringing AI into real work.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {OUTCOMES.map((outcome) => (
                <div
                  key={outcome.title}
                  className="border border-border bg-card/70 p-6 transition-colors hover:bg-card sm:p-7"
                >
                  <outcome.icon className="size-5 text-warn" />
                  <h3 className="mt-5 text-base font-semibold text-foreground">{outcome.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {outcome.body}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="how-it-works" className="border-b border-border">
          <div className="mx-auto w-full max-w-6xl px-6 py-16 sm:py-20">
            <h2 className="border-l-2 border-warn pl-4 font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
              A clear path to production
            </h2>
            <ol className="relative mt-10 space-y-10 before:absolute before:bottom-6 before:left-5 before:top-6 before:w-px before:bg-border sm:space-y-12">
              {STEPS.map((s) => (
                <li key={s.n} className="relative flex gap-5 pl-0">
                  <span className="z-10 flex size-10 shrink-0 items-center justify-center rounded-full border-2 border-border bg-background font-mono text-xs font-semibold text-muted-foreground first:border-warn first:text-warn">
                    {s.n}
                  </span>
                  <div className="pt-1">
                    <h3 className="text-base font-semibold text-foreground sm:text-lg">{s.t}</h3>
                    <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-muted-foreground">
                      {s.d}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-12">
              <Button asChild className="bg-warn text-warn-foreground hover:bg-warn/90">
                <Link to="/studio">
                  Open the Discovery Studio
                  <ArrowRight className="size-4" />
                </Link>
              </Button>
            </div>
          </div>
        </section>
      </main>

      <footer className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-4 px-6 py-10 text-xs text-muted-foreground">
        <span className="font-mono tracking-[0.16em]">SAGA·MCP</span>
        <span>Make room for AI. Keep the final say.</span>
      </footer>
    </div>
  );
}
