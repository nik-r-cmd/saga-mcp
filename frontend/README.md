# Saga-MCP Frontend

This React and TypeScript application provides the web interface for
Saga-MCP. It includes a project landing page and a control studio for
connecting to the backend, discovering and reviewing tools, approving
compensation mappings, submitting tasks, and viewing saga execution
events.

## Requirements

- Node.js and npm
- A running Saga-MCP backend for connected features
- Supabase project settings for authentication

## Local development

From this directory, install dependencies and create a local
environment file:

```powershell
npm ci
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in `.env`.
`VITE_BACKEND_URL` is optional and defaults to `http://localhost:8000`.
Start the development server with:

```powershell
npm run dev
```

## Available scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the local Vite development server |
| `npm run build` | Create a production build |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run ESLint |

The application depends on the backend's authenticated API and WebSocket
endpoints for connected functionality. See the repository
[README](../README.md) for backend setup, agent configuration, tests, and
evaluation instructions.
