export type Agent = {
  id: string;
  name: string;
  avatar: string;
  kind: "mock" | "cli" | "api";
  provider: string;
  capability_tags: string[];
  system_prompt: string;
  description: string;
  tools: string[];
  enabled: boolean;
  in_contacts: boolean;
  health: string;
  model?: string | null;
  is_builtin: boolean;
};

export type ConversationMember = Agent & {
  is_primary: boolean;
};

export type Conversation = {
  id: string;
  title: string;
  mode: "single" | "group";
  agent_ids: string[];
  pinned: boolean;
  archived: boolean;
  last_message: string;
  updated_at: string;
  created_at: string;
};

export type Artifact = {
  id: string;
  type: "code" | "image" | "file" | "web_preview" | "diff" | "document" | "slides" | "deploy" | "conflict";
  title: string;
  content: string;
  language?: string | null;
  status: "pending" | "accepted" | "declined";
};

export type Version = {
  id: string;
  message: string;
  timestamp: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "agent" | "orchestrator" | "system";
  sender_id: string;
  sender_name: string;
  content: string;
  artifacts: Artifact[];
  reply_to?: string | null;
  pinned: boolean;
  streaming: boolean;
  created_at: string;
  updated_at: string;
};

export type ConversationDetail = {
  conversation: Conversation;
  messages: Message[];
  members: ConversationMember[];
};
