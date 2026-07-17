from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

from .adapters import AdapterRegistry
from .models import (
    Agent,
    AgentCreate,
    ArtifactStatusUpdate,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    ConversationMember,
    ConversationMemberCreate,
    ConversationMemberUpdate,
    ConversationUpdate,
    Message,
    MessageCreate,
    SendMessageResponse,
    ToggleArchived,
    TogglePinned,
)
from .orchestrator import Orchestrator
from fastapi.staticfiles import StaticFiles

from .store import Store


app = FastAPI(title="AgentHub API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount projects workspace directory for hosting static websites
registry = AdapterRegistry()
projects_dir_path = registry.settings.workspace / "projects"
projects_dir_path.mkdir(parents=True, exist_ok=True)
app.mount("/static/projects", StaticFiles(directory=projects_dir_path), name="projects")

store = Store(Path(__file__).resolve().parents[1] / "agenthub.sqlite3")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/agents", response_model=list[Agent])
def list_agents() -> list[Agent]:
    registry = AdapterRegistry()
    agents = store.list_agents()
    for agent in agents:
        agent.health = registry.for_agent(agent).health(agent)
    return agents


@app.get("/api/contacts", response_model=list[Agent])
def list_contacts() -> list[Agent]:
    registry = AdapterRegistry()
    agents = store.list_contact_agents()
    for agent in agents:
        agent.health = registry.for_agent(agent).health(agent)
    return agents


@app.post("/api/agents", response_model=Agent)
def create_agent(payload: AgentCreate) -> Agent:
    if payload.provider == "custom":
        payload.provider = "deepseek"
    if payload.kind == "mock":
        payload.kind = "api"
    if not payload.model:
        payload.model = "deepseek-v4-pro"
    return store.create_agent(payload)

@app.delete("/api/contacts/{agent_id}", status_code=204, response_class=Response)
def remove_contact(agent_id: str) -> Response:
    agent = next(iter(store.get_agents([agent_id])), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.is_builtin:
        raise HTTPException(status_code=409, detail="Built-in contacts cannot be removed")
    if not store.remove_contact(agent_id):
        raise HTTPException(status_code=409, detail="Contact could not be removed")
    return Response(status_code=204)

@app.get("/api/agents/health")
def agent_health() -> dict[str, str]:
    registry = AdapterRegistry()
    return {agent.id: registry.for_agent(agent).health(agent) for agent in store.list_agents()}


@app.get("/api/conversations", response_model=list[Conversation])
def list_conversations(
    q: str | None = Query(default=None), include_archived: bool = Query(default=False)
) -> list[Conversation]:
    return store.list_conversations(query=q, include_archived=include_archived)


@app.post("/api/conversations", response_model=Conversation)
def create_conversation(payload: ConversationCreate) -> Conversation:
    conversation = store.create_conversation(payload)
    project_dir(conversation.id).mkdir(parents=True, exist_ok=True)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> ConversationDetail:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetail(
        conversation=conversation,
        messages=store.list_messages(conversation_id),
        members=store.list_conversation_agents(conversation_id),
    )


@app.patch("/api/conversations/{conversation_id}", response_model=Conversation)
def update_conversation(conversation_id: str, payload: ConversationUpdate) -> Conversation:
    if payload.title is not None and not payload.title.strip():
        raise HTTPException(status_code=422, detail="Conversation title cannot be empty")
    if payload.agent_ids is not None:
        known_ids = {agent.id for agent in store.get_agents(payload.agent_ids)}
        if known_ids != set(payload.agent_ids):
            raise HTTPException(status_code=422, detail="Unknown agent in conversation")
    conversation = store.update_conversation(
        conversation_id, title=payload.title, agent_ids=payload.agent_ids
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post(
    "/api/conversations/{conversation_id}/members",
    response_model=ConversationMember,
)
def add_conversation_member(
    conversation_id: str, payload: ConversationMemberCreate
) -> ConversationMember:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.mode != "group":
        raise HTTPException(status_code=409, detail="Only group conversations can add members")
    member = store.add_conversation_agent(conversation_id, payload.agent_id)
    if not member:
        raise HTTPException(status_code=404, detail="Agent not found")
    return member


@app.patch(
    "/api/conversations/{conversation_id}/members/{agent_id}",
    response_model=ConversationMember,
)
def update_conversation_member(
    conversation_id: str,
    agent_id: str,
    payload: ConversationMemberUpdate,
) -> ConversationMember:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.mode != "group":
        raise HTTPException(status_code=409, detail="Single conversation members are read-only")
    for value in (payload.name, payload.description, payload.system_prompt):
        if value is not None and not value.strip():
            raise HTTPException(status_code=422, detail="Member fields cannot be empty")
    member = store.update_conversation_agent(
        conversation_id,
        agent_id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        is_primary=payload.is_primary,
    )
    if not member:
        raise HTTPException(status_code=404, detail="Conversation member not found")
    return member


@app.delete("/api/conversations/{conversation_id}", status_code=204, response_class=Response)
def delete_conversation(conversation_id: str) -> Response:
    if not store.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@app.get("/api/conversations/{conversation_id}/download")
def download_project(conversation_id: str, background_tasks: BackgroundTasks) -> FileResponse:
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    registry = AdapterRegistry()
    target_dir = registry.settings.workspace / "projects" / conversation_id
    if not target_dir.is_dir():
        # Create an empty directory if it doesn't exist so we don't crash
        target_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = tempfile.mkdtemp()
    zip_path = Path(temp_dir) / f"project_{conversation_id}"

    try:
        archive_path_str = shutil.make_archive(
            base_name=str(zip_path),
            format="zip",
            root_dir=str(target_dir)
        )
        archive_path = Path(archive_path_str)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to create ZIP: {str(e)}")

    def cleanup_temp():
        shutil.rmtree(temp_dir, ignore_errors=True)

    background_tasks.add_task(cleanup_temp)
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        filename=f"project_{conversation_id}.zip"
    )


@app.patch("/api/conversations/{conversation_id}/pinned", response_model=Conversation)
def set_conversation_pinned(conversation_id: str, payload: TogglePinned) -> Conversation:
    conversation = store.update_conversation_flags(conversation_id, pinned=payload.pinned)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.patch("/api/conversations/{conversation_id}/archived", response_model=Conversation)
def set_conversation_archived(conversation_id: str, payload: ToggleArchived) -> Conversation:
    conversation = store.update_conversation_flags(conversation_id, archived=payload.archived)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def dispatch_message(db_path: Path, conversation_id: str, user_message: Message, parallel: bool = False) -> None:
    background_store = Store(db_path)
    try:
        background_orchestrator = Orchestrator(background_store, AdapterRegistry())
        background_orchestrator.dispatch(conversation_id, user_message, parallel)
    finally:
        background_store.conn.close()


def project_dir(conversation_id: str) -> Path:
    return AdapterRegistry().settings.workspace / "projects" / conversation_id


@app.post("/api/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
def send_message(
    conversation_id: str, payload: MessageCreate, background_tasks: BackgroundTasks
) -> SendMessageResponse:
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation = store.get_conversation(conversation_id)
    if conversation and conversation.archived:
        raise HTTPException(status_code=409, detail="Archived conversations are read-only")
    user_content = payload.content.strip().lower()
    is_deploy = user_content in {"部署", "deploy", "🚀 部署", "🚀部署", "部署当前项目", "deploy project"} or (
        user_content.startswith("部署") or user_content.startswith("deploy")
    )

    user_message = store.create_message(
        conversation_id,
        role="user",
        sender_id="user",
        sender_name="You",
        content=payload.content,
        reply_to=payload.reply_to,
    )
    if is_deploy:
        from .deployer import dispatch_deploy
        background_tasks.add_task(dispatch_deploy, store.db_path, conversation_id)
    else:
        parallel_flag = bool(payload.parallel)
        if any(w in user_content for w in ["并行", "并列", "parallel", "同时"]):
            parallel_flag = True
        background_tasks.add_task(dispatch_message, store.db_path, conversation_id, user_message, parallel_flag)
    return SendMessageResponse(user_message=user_message, agent_messages=[])


@app.get("/api/conversations/{conversation_id}/messages", response_model=list[Message])
def list_messages(conversation_id: str) -> list[Message]:
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return store.list_messages(conversation_id)


@app.patch("/api/messages/{message_id}/pinned", response_model=Message)
def set_message_pinned(message_id: str, payload: TogglePinned) -> Message:
    message = store.set_message_pinned(message_id, payload.pinned)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@app.patch("/api/conversations/{conversation_id}/messages/{message_id}/artifacts/{artifact_id}", response_model=Message)
def update_artifact(
    conversation_id: str, message_id: str, artifact_id: str, payload: ArtifactStatusUpdate
) -> Message:
    message = store.update_artifact_status(conversation_id, message_id, artifact_id, payload.status)
    if not message:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return message


@app.get("/api/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str):
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_stream():
        fingerprints: dict[str, str] = {}
        for _ in range(2400):
            messages = store.list_messages(conversation_id)
            for message in messages:
                fingerprint = f"{message.updated_at}:{message.streaming}:{len(message.content)}"
                if fingerprints.get(message.id) == fingerprint:
                    continue
                fingerprints[message.id] = fingerprint
                yield f"data: {json.dumps(message.model_dump(), ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Versioning & Conflict Resolution Endpoints ----

from typing import Literal
from pydantic import BaseModel

class ConflictResolvePayload(BaseModel):
    file: str
    action: Literal["keep_a", "keep_b", "manual"]
    manual_content: str | None = None


@app.get("/api/conversations/{conversation_id}/versions")
def get_versions(conversation_id: str):
    from .version_manager import VersionManager
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return VersionManager.list_versions(conversation_id)


@app.post("/api/conversations/{conversation_id}/versions/{version_id}/revert")
def revert_version(conversation_id: str, version_id: str):
    from .version_manager import VersionManager
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        VersionManager.revert_version(conversation_id, version_id)
        return {"status": "ok"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/conversations/{conversation_id}/files")
def get_files(conversation_id: str):
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    registry = AdapterRegistry()
    target_dir = registry.settings.workspace / "projects" / conversation_id
    if not target_dir.is_dir():
        return []

    # Recursively list files, excluding .versions and temp directories
    file_list = []
    import os
    for root, _, files in os.walk(target_dir):
        for file in files:
            full_path = Path(root) / file
            rel_path = str(full_path.relative_to(target_dir))
            # Ignore hidden files, .versions and temporary files
            if rel_path.startswith(".versions") or ".versions\\" in rel_path or ".versions/" in rel_path:
                continue
            if "_tmp_" in rel_path or "_agent_" in rel_path:
                continue
            file_list.append(rel_path)
    return sorted(file_list)


@app.get("/api/conversations/{conversation_id}/files/content/{file_path:path}")
def get_file_content(conversation_id: str, file_path: str):
    if not store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    registry = AdapterRegistry()
    target_dir = (registry.settings.workspace / "projects" / conversation_id).resolve()
    requested = (target_dir / file_path).resolve()
    if target_dir not in requested.parents and requested != target_dir:
        raise HTTPException(status_code=400, detail="File path escapes conversation workspace")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"path": file_path, "content": requested.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"path": file_path, "content": requested.read_text(encoding="utf-8", errors="replace")}


@app.post("/api/conversations/{conversation_id}/messages/{message_id}/artifacts/{artifact_id}/resolve")
def resolve_conflict(
    conversation_id: str,
    message_id: str,
    artifact_id: str,
    payload: ConflictResolvePayload,
):
    import json
    from .version_manager import VersionManager
    message = store.update_artifact_status(conversation_id, message_id, artifact_id, "accepted")
    if not message:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Find the conflict details from the artifact content
    conflict_artifact = next((art for art in message.artifacts if art.id == artifact_id), None)
    if not conflict_artifact:
        raise HTTPException(status_code=404, detail="Conflict artifact not found")

    try:
        conflicts = json.loads(conflict_artifact.content)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid conflict artifact content")

    target_conflict = next((c for c in conflicts if c["file"] == payload.file), None)
    if not target_conflict:
        raise HTTPException(status_code=404, detail="Specified file not found in conflicts list")

    registry = AdapterRegistry()
    workspace_dir = registry.settings.workspace / "projects" / conversation_id
    file_path = workspace_dir / payload.file

    # Resolve and write chosen content
    if payload.action == "keep_a":
        resolved_content = target_conflict["agent_a"]
    elif payload.action == "keep_b":
        resolved_content = target_conflict["agent_b"]
    else:
        if payload.manual_content is None:
            raise HTTPException(status_code=400, detail="manual_content is required for manual resolution")
        resolved_content = payload.manual_content

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(resolved_content, encoding="utf-8")

    # Update the artifact status or content to show resolved
    remaining_conflicts = [c for c in conflicts if c["file"] != payload.file]
    if remaining_conflicts:
        conflict_artifact.content = json.dumps(remaining_conflicts, ensure_ascii=False)
        store.update_message_content(message_id, message.content, artifacts=message.artifacts)
    else:
        # All conflicts resolved! Update message content
        conflict_artifact.content = "[]"
        store.update_message_content(
            message_id,
            "🎉 并行合并冲突已全部解决！",
            artifacts=message.artifacts,
            streaming=False
        )

    # Save a version snapshot
    VersionManager.save_version(
        conversation_id,
        f"Conflict in {payload.file} resolved via {payload.action}"
    )

    return {"status": "ok"}
