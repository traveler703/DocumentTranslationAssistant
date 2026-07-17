# AgentHub Frontend

React + Vite client for the AgentHub demo.

The UI provides group chats, reusable Agent contacts, archived read-only conversations,
current-conversation members, editable conversation titles, and a complete custom Agent form.
New groups start with System Agent, the member panel can add contacts with `+`, and member cards
open single-chat read-only details or group-chat session-specific editing and primary-Agent controls.
The contacts view can remove user-created contacts while preserving built-in contacts, existing
conversation members, and chat history.
Agent messages update incrementally over SSE and render with GitHub Flavored Markdown. On desktop,
the sidebar, message stream, and member panel are independent scroll containers.

```bash
npm install
npm test
npm run dev
```

The dev server proxies `/api` to `http://127.0.0.1:8000`.
