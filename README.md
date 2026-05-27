# Personal Agent

Local-first personal AI Agent system scaffold. It follows the requested stack:
FastAPI, LangGraph-ready runtime boundaries, React + Vite + TypeScript,
PostgreSQL, pgvector, local plugins, and light background scheduling with
APScheduler.

## Structure

- `backend/app/main.py`: FastAPI application and runtime startup.
- `backend/app/agent/runtime.py`: streaming Agent Runtime entrypoint.
- `backend/app/services/model_gateway.py`: embedding and chat model routing.
- `backend/app/services/rag.py`: first-pass RAG indexing and retrieval boundary.
- `backend/app/services/plugin_registry.py`: local manifest-based plugin loading.
- `backend/migrations`: Alembic schema, including `pgvector`.
- `frontend/src/App.tsx`: chat workspace with conversations, streaming, tools, citations.
- `plugins/read_file`: sample local plugin.

## Local Setup

Prerequisites: Python 3.11+, Node.js 20+, local PostgreSQL with pgvector
available.

```powershell
cd D:\workplace\AgentDemo\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
Copy-Item .env.example .env
```

This repository can run PostgreSQL from a local binary package without Docker:

```powershell
cd D:\workplace\AgentDemo
New-Item -ItemType Directory -Force .local\postgres | Out-Null
Expand-Archive -LiteralPath downloads\postgresql-16.14-1-windows-x64-binaries.zip -DestinationPath .local\postgres -Force
.\.local\postgres\pgsql\bin\initdb.exe -D .local\postgres\data -U agent -A trust --encoding=UTF8 --locale=C
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\postgres\data -l .local\postgres\postgres.log start
.\.local\postgres\pgsql\bin\createdb.exe -U agent agent_demo
```

If pgvector is not bundled with the PostgreSQL zip, install a PostgreSQL 16
Windows pgvector package by copying its `lib`, `share\extension`, and
`include\server\extension\vector` contents into `.local\postgres\pgsql`, then:

```powershell
.\.local\postgres\pgsql\bin\psql.exe -U agent -d agent_demo -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Then run migrations:

```powershell
alembic upgrade head
```

Start the backend:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Start the frontend:

```powershell
cd D:\workplace\AgentDemo\frontend
npm install
npm run dev
```

Open `http://localhost:5173` on this machine, or
`http://<this-machine-ip>:5173` from another device on the same network.
The Vite dev server proxies `/api` to the local backend, so remote browsers can
use the page and chat through the same frontend address.

For temporary public internet access without router port forwarding, run a tunnel
to the frontend:

```powershell
.\.local\cloudflared\cloudflared.exe tunnel --url http://localhost:5173
```

Open the generated `https://*.trycloudflare.com` URL and enter the
`AGENT_ACCESS_TOKEN` configured in `backend\.env`. In this local setup, the
generated access token is also stored in `.local\agent-access-token.txt`.

## Plugin Manifest

Each local plugin lives under `plugins/<plugin_name>/manifest.json` and declares:

- `name`
- `description`
- `parameters`
- `permission`
- `timeout_seconds`
- `entrypoint`
- `enabled`

The first version loads only local plugins. Remote installation and plugin market
features are intentionally out of scope.

## Model Configuration

The first runtime uses OpenAI-compatible HTTP APIs:

- Embedding: `OPENAI_BASE_URL` + `OPENAI_EMBEDDING_MODEL`
- Agent chat: `LLM_BASE_URL` + `LLM_CHAT_MODEL`

Use `text-embedding-3-small` by default, or switch `OPENAI_EMBEDDING_MODEL`
to the larger embedding model you have enabled. Agent chat is routed to the
DeepSeek-compatible `LLM_CHAT_MODEL` configured in `backend/.env`.

## Current Runtime Behavior

The Agent Runtime persists user and assistant messages, loads recent messages
from the active conversation as multi-turn memory, performs embedding-first
knowledge search with keyword fallback, routes chat through `ModelGateway`, and
streams model tokens back over SSE. LangGraph node wiring and durable task lookup
are the next implementation layer.

## Tests

```powershell
cd D:\workplace\AgentDemo\backend
pytest
```
