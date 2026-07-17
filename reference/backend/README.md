# AgentHub Backend

FastAPI + SQLite service for the AgentHub demo.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn agenthub.main:app --reload --port 8000
```

The default database is `backend/agenthub.sqlite3` and is seeded with the three protected built-in
contacts: System Agent, Claude Code, and Codex. Existing user-created contacts are preserved across
upgrades and can be removed from the contact list without deleting conversation history.

Configure DeepSeek for user-created agents in the project root `.env`:

```bash
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
AGENTHUB_WORKSPACE=/absolute/path/to/AgentHub
AGENTHUB_AGENT_TIMEOUT_SECONDS=300
CLAUDE_CLI_PATH=/usr/local/bin/claude
CODEX_CLI_PATH=/Applications/Codex.app/Contents/Resources/codex
```

Claude Code uses `claude --print`; Codex uses `codex exec`. Both commands must already be installed and logged in on the host. AgentHub can discover common macOS install paths, while the two CLI path variables provide explicit overrides.

Agent output is streamed through SSE. Generated files are isolated by conversation under
`projects/{conversation_id}/`; Claude Code and Codex run with that directory as their working root.
Their prompts are passed through stdin rather than command-line arguments, so long conversation
history does not exceed the operating system argument limit. Transport JSON and thinking events are
filtered from chat output.

New group chats start with System Agent as the only member and primary Agent. Contact defaults live
in `agents`; conversation-specific names, responsibilities, system prompts, and primary status live
in `conversation_members`, so group edits do not alter reusable contact data.

The active primary Agent plans through its own adapter: System Agent and user-created API Agents use
DeepSeek, while Claude Code and Codex use their corresponding authenticated CLI. Planning and
execution failures are surfaced in chat and eligible work is transferred to another available Agent.
File-writing tasks are routed to CLI Agents. Successful text-only API Agent responses are archived
under the same conversation project directory.
