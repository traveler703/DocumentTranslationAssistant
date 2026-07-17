from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


AgentKind = Literal["mock", "cli", "api"]
ConversationMode = Literal["single", "group"]
MessageRole = Literal["user", "agent", "orchestrator", "system"]
ArtifactType = Literal["code", "image", "file", "web_preview", "diff", "document", "slides", "deploy", "conflict"]
ArtifactStatus = Literal["pending", "accepted", "declined"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Agent(BaseModel):
    id: str
    name: str
    avatar: str
    kind: AgentKind = "mock"
    provider: str
    capability_tags: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    in_contacts: bool = True
    health: str = "ok"
    model: str | None = None
    is_builtin: bool = False


class AgentCreate(BaseModel):
    name: str
    avatar: str = "AI"
    kind: AgentKind = "mock"
    provider: str = "custom"
    capability_tags: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    in_contacts: bool = True
    model: str | None = None


class ConversationMember(Agent):
    is_primary: bool = False


class ConversationMemberCreate(BaseModel):
    agent_id: str


class ConversationMemberUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    is_primary: bool | None = None


class Artifact(BaseModel):
    id: str
    type: ArtifactType
    title: str
    content: str
    language: str | None = None
    status: ArtifactStatus = "pending"


class Message(BaseModel):
    id: str
    conversation_id: str
    role: MessageRole
    sender_id: str
    sender_name: str
    content: str
    artifacts: list[Artifact] = Field(default_factory=list)
    reply_to: str | None = None
    pinned: bool = False
    streaming: bool = False
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class Conversation(BaseModel):
    id: str
    title: str
    mode: ConversationMode
    agent_ids: list[str] = Field(default_factory=list)
    pinned: bool = False
    archived: bool = False
    last_message: str = ""
    updated_at: str = Field(default_factory=utc_now)
    created_at: str = Field(default_factory=utc_now)


class ConversationCreate(BaseModel):
    title: str
    mode: ConversationMode = "group"
    agent_ids: list[str] = Field(default_factory=list)


class ConversationUpdate(BaseModel):
    title: str | None = None
    agent_ids: list[str] | None = None


class MessageCreate(BaseModel):
    content: str
    reply_to: str | None = None
    parallel: bool | None = None


class TogglePinned(BaseModel):
    pinned: bool


class ToggleArchived(BaseModel):
    archived: bool


class ArtifactStatusUpdate(BaseModel):
    status: ArtifactStatus


class ConversationDetail(BaseModel):
    conversation: Conversation
    messages: list[Message]
    members: list[ConversationMember] = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    user_message: Message
    agent_messages: list[Message]
