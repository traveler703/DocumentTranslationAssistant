from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable

from .models import (
    Agent,
    AgentCreate,
    Artifact,
    ArtifactStatus,
    Conversation,
    ConversationCreate,
    ConversationMember,
    Message,
    utc_now,
)


import functools
import threading

def db_write(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return func(self, *args, **kwargs)
    return wrapper


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "agenthub.sqlite3"


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.lock = threading.RLock()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self.seed_defaults()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                avatar TEXT NOT NULL,
                kind TEXT NOT NULL,
                provider TEXT NOT NULL,
                capability_tags TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tools TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                in_contacts INTEGER NOT NULL DEFAULT 1,
                health TEXT NOT NULL,
                model TEXT,
                is_builtin INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                agent_ids TEXT NOT NULL,
                pinned INTEGER NOT NULL,
                archived INTEGER NOT NULL,
                last_message TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                content TEXT NOT NULL,
                artifacts TEXT NOT NULL,
                reply_to TEXT,
                pinned INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                streaming INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(conversation_id, agent_id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id),
                FOREIGN KEY(agent_id) REFERENCES agents(id)
            );
            """
        )
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "model" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN model TEXT")
        if "description" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "in_contacts" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN in_contacts INTEGER NOT NULL DEFAULT 1")
        if "is_builtin" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0")
        self.conn.execute(
            "UPDATE agents SET is_builtin = 1 WHERE id IN ('system-agent', 'claude-code', 'codex')"
        )
        self.conn.execute("DROP TABLE IF EXISTS todos")
        message_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "streaming" not in message_columns:
            self.conn.execute("ALTER TABLE messages ADD COLUMN streaming INTEGER NOT NULL DEFAULT 0")
        if "updated_at" not in message_columns:
            self.conn.execute("ALTER TABLE messages ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            self.conn.execute("UPDATE messages SET updated_at = created_at WHERE updated_at = ''")
        self.conn.commit()

    def seed_defaults(self) -> None:
        defaults = [
            Agent(
                id="system-agent",
                name="System Agent",
                avatar="SA",
                kind="api",
                provider="deepseek",
                capability_tags=["统筹", "规划", "调度"],
                system_prompt=(
                    "你是 System Agent。先阅读当前会话全部历史、置顶消息和本提示词，"
                    "再理解需求、拆解任务并按顺序分配给合适的会话成员。"
                ),
                description="负责理解需求、拆解任务、统筹规划并依次调度当前会话中的 Agent。",
                tools=["deepseek-v4-pro"],
                model="deepseek-v4-pro",
                is_builtin=True,
            ),
            Agent(
                id="claude-code",
                name="Claude Code",
                avatar="CC",
                kind="cli",
                provider="claude-code",
                capability_tags=["架构", "重构", "长上下文"],
                system_prompt="你是 Claude Code，负责需求拆解、架构设计、代码审查和长上下文推理。",
                description="负责架构设计、复杂重构、代码审查和长上下文任务。",
                tools=["claude", "read", "edit", "diff"],
                model="sonnet",
                is_builtin=True,
            ),
            Agent(
                id="codex",
                name="Codex",
                avatar="CX",
                kind="cli",
                provider="codex",
                capability_tags=["实现", "测试", "前端"],
                system_prompt="你是 Codex，负责把用户任务转化为可运行代码、测试和验证步骤。",
                description="负责代码实现、测试、调试和前端交互验证。",
                tools=["codex", "terminal", "patch"],
                is_builtin=True,
            ),
        ]
        for agent in defaults:
            self.create_agent_from_model(agent)

        self.conn.execute("UPDATE agents SET enabled = 0, health = 'disabled' WHERE provider = 'opencode'")
        self.conn.commit()

        count = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        if not count:
            conversation = self.create_conversation(
                ConversationCreate(
                    title="AgentHub 真实 Agent 群聊",
                    mode="group",
                    agent_ids=[agent.id for agent in defaults],
                )
            )
            self.create_message(
                conversation.id,
                role="system",
                sender_id="system",
                sender_name="AgentHub",
                content=(
                    "欢迎来到 AgentHub。当前已接入 Claude Code 与 Codex。"
                    "可以 @all 发起群聊协作，也可以 @Codex 或 @Claude Code 指定 Agent。"
                ),
            )
        self._migrate_conversation_members()

    @db_write
    def create_agent_from_model(self, agent: Agent) -> Agent:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO agents (
                id, name, avatar, kind, provider, capability_tags, system_prompt,
                description, tools, enabled, in_contacts, health, model, is_builtin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent.id,
                agent.name,
                agent.avatar,
                agent.kind,
                agent.provider,
                json.dumps(agent.capability_tags, ensure_ascii=False),
                agent.system_prompt,
                agent.description,
                json.dumps(agent.tools, ensure_ascii=False),
                1 if agent.enabled else 0,
                1 if agent.in_contacts else 0,
                agent.health,
                agent.model,
                1 if agent.is_builtin else 0,
            ),
        )
        self.conn.commit()
        return agent

    @db_write
    def create_agent(self, payload: AgentCreate) -> Agent:
        slug = payload.name.lower().replace(" ", "-")
        agent = Agent(id=f"agent-{slug}-{uuid.uuid4().hex[:6]}", **payload.model_dump())
        return self.create_agent_from_model(agent)

    def list_agents(self) -> list[Agent]:
        rows = self.conn.execute("SELECT * FROM agents ORDER BY enabled DESC, name").fetchall()
        return [self._agent_from_row(row) for row in rows]

    def list_enabled_agents(self) -> list[Agent]:
        return [agent for agent in self.list_agents() if agent.enabled]

    def list_contact_agents(self) -> list[Agent]:
        return [agent for agent in self.list_agents() if agent.enabled and agent.in_contacts]

    def remove_contact(self, agent_id: str) -> bool:
        agents = self.get_agents([agent_id])
        if not agents or agents[0].is_builtin:
            return False
        self.conn.execute(
            "UPDATE agents SET in_contacts = 0 WHERE id = ?",
            (agent_id,),
        )
        self.conn.commit()
        return True

    def get_agents(self, agent_ids: Iterable[str]) -> list[Agent]:
        ids = list(agent_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(f"SELECT * FROM agents WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {row["id"]: self._agent_from_row(row) for row in rows}
        return [by_id[agent_id] for agent_id in ids if agent_id in by_id]

    def get_agent_by_name(self, name: str) -> Agent | None:
        rows = self.conn.execute("SELECT * FROM agents").fetchall()
        normalized = name.casefold()
        for row in rows:
            agent = self._agent_from_row(row)
            if agent.name.casefold() == normalized or agent.id.casefold() == normalized:
                return agent
        return None

    @db_write
    def create_conversation(self, payload: ConversationCreate) -> Conversation:
        now = utc_now()
        agent_ids = list(dict.fromkeys(payload.agent_ids))
        if payload.mode == "group" and not agent_ids:
            agent_ids = ["system-agent"]
        conversation = Conversation(
            id=f"conv-{uuid.uuid4().hex[:10]}",
            title=payload.title,
            mode=payload.mode,
            agent_ids=agent_ids,
            updated_at=now,
            created_at=now,
        )
        self.conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation.id,
                conversation.title,
                conversation.mode,
                json.dumps(conversation.agent_ids, ensure_ascii=False),
                0,
                0,
                "",
                conversation.updated_at,
                conversation.created_at,
            ),
        )
        primary_id = (
            "system-agent"
            if conversation.mode == "group" and "system-agent" in conversation.agent_ids
            else (conversation.agent_ids[0] if conversation.agent_ids else None)
        )
        self._replace_conversation_members(
            conversation.id,
            conversation.agent_ids,
            primary_agent_id=primary_id,
        )
        self.conn.commit()
        return conversation

    def list_conversations(self, query: str | None = None, include_archived: bool = False) -> list[Conversation]:
        sql = "SELECT * FROM conversations"
        params: list[str | int] = []
        clauses = []
        if not include_archived:
            clauses.append("archived = 0")
        if query:
            clauses.append("(title LIKE ? OR last_message LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY pinned DESC, updated_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return self._conversation_from_row(row) if row else None

    @db_write
    def update_conversation_flags(
        self, conversation_id: str, *, pinned: bool | None = None, archived: bool | None = None
    ) -> Conversation | None:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None
        values = {
            "pinned": conversation.pinned if pinned is None else pinned,
            "archived": conversation.archived if archived is None else archived,
        }
        self.conn.execute(
            "UPDATE conversations SET pinned = ?, archived = ? WHERE id = ?",
            (1 if values["pinned"] else 0, 1 if values["archived"] else 0, conversation_id),
        )
        self.conn.commit()
        return self.get_conversation(conversation_id)

    @db_write
    def update_conversation(
        self, conversation_id: str, *, title: str | None = None, agent_ids: list[str] | None = None
    ) -> Conversation | None:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None
        next_title = conversation.title if title is None else title.strip()
        next_agent_ids = conversation.agent_ids if agent_ids is None else agent_ids
        self.conn.execute(
            "UPDATE conversations SET title = ?, agent_ids = ?, updated_at = ? WHERE id = ?",
            (next_title, json.dumps(next_agent_ids, ensure_ascii=False), utc_now(), conversation_id),
        )
        if agent_ids is not None:
            current_primary = self.get_primary_conversation_agent(conversation_id)
            primary_id = current_primary.id if current_primary and current_primary.id in agent_ids else None
            if primary_id is None and agent_ids:
                primary_id = "system-agent" if "system-agent" in agent_ids else agent_ids[0]
            self._replace_conversation_members(
                conversation_id,
                agent_ids,
                primary_agent_id=primary_id,
                preserve_existing=True,
            )
        self.conn.commit()
        return self.get_conversation(conversation_id)

    @db_write
    def delete_conversation(self, conversation_id: str) -> bool:
        if not self.get_conversation(conversation_id):
            return False
        self.conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self.conn.execute("DELETE FROM conversation_members WHERE conversation_id = ?", (conversation_id,))
        self.conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self.conn.commit()
        return True

    def list_conversation_agents(self, conversation_id: str) -> list[ConversationMember]:
        rows = self.conn.execute(
            """
            SELECT a.*, cm.name AS member_name, cm.description AS member_description,
                   cm.system_prompt AS member_system_prompt, cm.is_primary
            FROM conversation_members cm
            JOIN agents a ON a.id = cm.agent_id
            WHERE cm.conversation_id = ?
            ORDER BY cm.position, a.name
            """,
            (conversation_id,),
        ).fetchall()
        return [self._conversation_member_from_row(row) for row in rows]

    def get_primary_conversation_agent(self, conversation_id: str) -> ConversationMember | None:
        return next(
            (member for member in self.list_conversation_agents(conversation_id) if member.is_primary),
            None,
        )

    def add_conversation_agent(self, conversation_id: str, agent_id: str) -> ConversationMember | None:
        conversation = self.get_conversation(conversation_id)
        agents = self.get_agents([agent_id])
        if not conversation or not agents:
            return None
        if agent_id in conversation.agent_ids:
            return next(
                (member for member in self.list_conversation_agents(conversation_id) if member.id == agent_id),
                None,
            )
        next_ids = [*conversation.agent_ids, agent_id]
        self.update_conversation(conversation_id, agent_ids=next_ids)
        return next(
            (member for member in self.list_conversation_agents(conversation_id) if member.id == agent_id),
            None,
        )

    def update_conversation_agent(
        self,
        conversation_id: str,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        is_primary: bool | None = None,
    ) -> ConversationMember | None:
        members = self.list_conversation_agents(conversation_id)
        target = next((member for member in members if member.id == agent_id), None)
        if not target:
            return None
        next_name = target.name if name is None else name.strip()
        next_description = target.description if description is None else description.strip()
        next_prompt = target.system_prompt if system_prompt is None else system_prompt.strip()
        if is_primary and not target.is_primary:
            previous = next((member for member in members if member.is_primary), None)
            if previous:
                self.conn.execute(
                    """
                    UPDATE conversation_members
                    SET description = ?, system_prompt = ?, is_primary = 0
                    WHERE conversation_id = ? AND agent_id = ?
                    """,
                    (next_description, next_prompt, conversation_id, previous.id),
                )
                next_description = previous.description
                next_prompt = previous.system_prompt
            self.conn.execute(
                "UPDATE conversation_members SET is_primary = 0 WHERE conversation_id = ?",
                (conversation_id,),
            )
        self.conn.execute(
            """
            UPDATE conversation_members
            SET name = ?, description = ?, system_prompt = ?, is_primary = ?
            WHERE conversation_id = ? AND agent_id = ?
            """,
            (
                next_name,
                next_description,
                next_prompt,
                1 if (is_primary or (is_primary is None and target.is_primary)) else 0,
                conversation_id,
                agent_id,
            ),
        )
        self.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (utc_now(), conversation_id),
        )
        self.conn.commit()
        return next(
            (member for member in self.list_conversation_agents(conversation_id) if member.id == agent_id),
            None,
        )

    def _replace_conversation_members(
        self,
        conversation_id: str,
        agent_ids: list[str],
        *,
        primary_agent_id: str | None,
        preserve_existing: bool = False,
    ) -> None:
        existing = {
            member.id: member for member in self.list_conversation_agents(conversation_id)
        } if preserve_existing else {}
        self.conn.execute(
            "DELETE FROM conversation_members WHERE conversation_id = ?",
            (conversation_id,),
        )
        for position, agent in enumerate(self.get_agents(agent_ids)):
            snapshot = existing.get(agent.id)
            self.conn.execute(
                """
                INSERT INTO conversation_members (
                    conversation_id, agent_id, name, description, system_prompt, is_primary, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    agent.id,
                    snapshot.name if snapshot else agent.name,
                    snapshot.description if snapshot else agent.description,
                    snapshot.system_prompt if snapshot else agent.system_prompt,
                    1 if agent.id == primary_agent_id else 0,
                    position,
                ),
            )

    def _migrate_conversation_members(self) -> None:
        conversations = self.conn.execute("SELECT * FROM conversations").fetchall()
        for row in conversations:
            conversation = self._conversation_from_row(row)
            known_ids = {agent.id for agent in self.get_agents(conversation.agent_ids)}
            ids = [agent_id for agent_id in conversation.agent_ids if agent_id in known_ids]
            if conversation.mode == "group" and "system-agent" not in ids:
                ids.insert(0, "system-agent")
            if ids != conversation.agent_ids:
                self.conn.execute(
                    "UPDATE conversations SET agent_ids = ? WHERE id = ?",
                    (json.dumps(ids, ensure_ascii=False), conversation.id),
                )
            member_rows = self.conn.execute(
                "SELECT agent_id FROM conversation_members WHERE conversation_id = ?",
                (conversation.id,),
            ).fetchall()
            member_ids = [member_row["agent_id"] for member_row in member_rows]
            if member_ids != ids:
                primary_id = (
                    "system-agent"
                    if conversation.mode == "group" and "system-agent" in ids
                    else (ids[0] if ids else None)
                )
                self._replace_conversation_members(
                    conversation.id,
                    ids,
                    primary_agent_id=primary_id,
                    preserve_existing=True,
                )
        self.conn.commit()

    @db_write
    def create_message(
        self,
        conversation_id: str,
        *,
        role: str,
        sender_id: str,
        sender_name: str,
        content: str,
        artifacts: list[Artifact] | None = None,
        reply_to: str | None = None,
        pinned: bool = False,
        streaming: bool = False,
    ) -> Message:
        now = utc_now()
        message = Message(
            id=f"msg-{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            role=role,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            artifacts=artifacts or [],
            reply_to=reply_to,
            pinned=pinned,
            streaming=streaming,
            created_at=now,
            updated_at=now,
        )
        self.conn.execute(
            """
            INSERT INTO messages (
                id, conversation_id, role, sender_id, sender_name, content,
                artifacts, reply_to, pinned, created_at, streaming, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                conversation_id,
                role,
                sender_id,
                sender_name,
                content,
                json.dumps([artifact.model_dump() for artifact in message.artifacts], ensure_ascii=False),
                reply_to,
                1 if pinned else 0,
                now,
                1 if streaming else 0,
                now,
            ),
        )
        self.conn.execute(
            "UPDATE conversations SET last_message = ?, updated_at = ? WHERE id = ?",
            (content[:160], now, conversation_id),
        )
        self.conn.commit()
        return message

    @db_write
    def update_message_content(
        self,
        message_id: str,
        content: str,
        *,
        artifacts: list[Artifact] | None = None,
        streaming: bool | None = None,
    ) -> Message | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            return None
        message = self._message_from_row(row)
        next_artifacts = message.artifacts if artifacts is None else artifacts
        next_streaming = message.streaming if streaming is None else streaming
        now = utc_now()
        self.conn.execute(
            """
            UPDATE messages
            SET content = ?, artifacts = ?, streaming = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                content,
                json.dumps([artifact.model_dump() for artifact in next_artifacts], ensure_ascii=False),
                1 if next_streaming else 0,
                now,
                message_id,
            ),
        )
        self.conn.execute(
            "UPDATE conversations SET last_message = ?, updated_at = ? WHERE id = ?",
            (content[:160], now, message.conversation_id),
        )
        self.conn.commit()
        updated = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._message_from_row(updated) if updated else None

    def list_messages(self, conversation_id: str) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (conversation_id,)
        ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def set_message_pinned(self, message_id: str, pinned: bool) -> Message | None:
        self.conn.execute("UPDATE messages SET pinned = ? WHERE id = ?", (1 if pinned else 0, message_id))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._message_from_row(row) if row else None

    def update_artifact_status(
        self, conversation_id: str, message_id: str, artifact_id: str, status: ArtifactStatus
    ) -> Message | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND id = ?", (conversation_id, message_id)
        ).fetchone()
        if not row:
            return None
        message = self._message_from_row(row)
        changed = False
        for artifact in message.artifacts:
            if artifact.id == artifact_id:
                artifact.status = status
                changed = True
        if not changed:
            return None
        self.conn.execute(
            "UPDATE messages SET artifacts = ? WHERE id = ?",
            (json.dumps([artifact.model_dump() for artifact in message.artifacts], ensure_ascii=False), message_id),
        )
        self.conn.commit()
        return message

    def _agent_from_row(self, row: sqlite3.Row) -> Agent:
        return Agent(
            id=row["id"],
            name=row["name"],
            avatar=row["avatar"],
            kind=row["kind"],
            provider=row["provider"],
            capability_tags=json.loads(row["capability_tags"]),
            system_prompt=row["system_prompt"],
            description=row["description"] if "description" in row.keys() else "",
            tools=json.loads(row["tools"]),
            enabled=bool(row["enabled"]),
            in_contacts=bool(row["in_contacts"]) if "in_contacts" in row.keys() else True,
            health=row["health"],
            model=row["model"] if "model" in row.keys() else None,
            is_builtin=bool(row["is_builtin"]) if "is_builtin" in row.keys() else False,
        )

    def _conversation_member_from_row(self, row: sqlite3.Row) -> ConversationMember:
        values = self._agent_from_row(row).model_dump()
        values.update(
            name=row["member_name"],
            description=row["member_description"],
            system_prompt=row["member_system_prompt"],
            is_primary=bool(row["is_primary"]),
        )
        return ConversationMember(**values)

    def _conversation_from_row(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            mode=row["mode"],
            agent_ids=json.loads(row["agent_ids"]),
            pinned=bool(row["pinned"]),
            archived=bool(row["archived"]),
            last_message=row["last_message"],
            updated_at=row["updated_at"],
            created_at=row["created_at"],
        )

    def _message_from_row(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            content=row["content"],
            artifacts=[Artifact(**item) for item in json.loads(row["artifacts"])],
            reply_to=row["reply_to"],
            pinned=bool(row["pinned"]),
            streaming=bool(row["streaming"]) if "streaming" in row.keys() else False,
            created_at=row["created_at"],
            updated_at=(
                row["updated_at"]
                if "updated_at" in row.keys() and row["updated_at"]
                else row["created_at"]
            ),
        )
