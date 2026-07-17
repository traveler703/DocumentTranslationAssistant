import type { Agent, Artifact, Conversation, ConversationDetail, ConversationMember, Message } from "./types";

const jsonHeaders = { "Content-Type": "application/json" };

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  agents: () => request<Agent[]>("/api/agents"),
  contacts: () => request<Agent[]>("/api/contacts"),
  createAgent: (payload: Partial<Agent>) =>
    request<Agent>("/api/agents", { method: "POST", headers: jsonHeaders, body: JSON.stringify(payload) }),
  removeContact: async (agentId: string) => {
    const response = await fetch(`/api/contacts/${agentId}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await response.text());
  },
  conversations: (query = "", includeArchived = false) => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (includeArchived) params.set("include_archived", "true");
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<Conversation[]>(`/api/conversations${suffix}`);
  },
  createConversation: (payload: { title: string; mode: "single" | "group"; agent_ids: string[] }) =>
    request<Conversation>("/api/conversations", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload)
    }),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  updateConversation: (id: string, payload: { title?: string; agent_ids?: string[] }) =>
    request<Conversation>(`/api/conversations/${id}`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify(payload)
    }),
  addConversationMember: (id: string, agentId: string) =>
    request<ConversationMember>(`/api/conversations/${id}/members`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ agent_id: agentId })
    }),
  updateConversationMember: (
    id: string,
    agentId: string,
    payload: { name?: string; description?: string; system_prompt?: string; is_primary?: boolean }
  ) =>
    request<ConversationMember>(`/api/conversations/${id}/members/${agentId}`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify(payload)
    }),
  deleteConversation: async (id: string) => {
    const response = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await response.text());
  },
  pinConversation: (id: string, pinned: boolean) =>
    request<Conversation>(`/api/conversations/${id}/pinned`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify({ pinned })
    }),
  archiveConversation: (id: string, archived: boolean) =>
    request<Conversation>(`/api/conversations/${id}/archived`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify({ archived })
    }),
  sendMessage: (conversationId: string, content: string, reply_to?: string | null, parallel?: boolean) =>
    request<{ user_message: Message; agent_messages: Message[] }>(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ content, reply_to, parallel })
    }),
  conversationEvents: (conversationId: string) =>
    new EventSource(`/api/conversations/${conversationId}/events`),
  pinMessage: (id: string, pinned: boolean) =>
    request<Message>(`/api/messages/${id}/pinned`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify({ pinned })
    }),
  updateArtifact: (conversationId: string, messageId: string, artifact: Artifact, status: Artifact["status"]) =>
    request<Message>(`/api/conversations/${conversationId}/messages/${messageId}/artifacts/${artifact.id}`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify({ status })
    }),
  downloadProjectUrl: (conversationId: string) => `/api/conversations/${conversationId}/download`,
  versions: (conversationId: string) =>
    request<any[]>(`/api/conversations/${conversationId}/versions`),
  revertVersion: (conversationId: string, versionId: string) =>
    request<{ status: string }>(`/api/conversations/${conversationId}/versions/${versionId}/revert`, {
      method: "POST",
      headers: jsonHeaders
    }),
  files: (conversationId: string) =>
    request<string[]>(`/api/conversations/${conversationId}/files`),
  fileContent: (conversationId: string, filePath: string) =>
    request<{ path: string; content: string }>(
      `/api/conversations/${conversationId}/files/content/${encodeURI(filePath)}`
    ),
  resolveConflict: (
    conversationId: string,
    messageId: string,
    artifactId: string,
    payload: { file: string; action: "keep_a" | "keep_b" | "manual"; manual_content?: string }
  ) =>
    request<{ status: string }>(
      `/api/conversations/${conversationId}/messages/${messageId}/artifacts/${artifactId}/resolve`,
      {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify(payload)
      }
    )
};
