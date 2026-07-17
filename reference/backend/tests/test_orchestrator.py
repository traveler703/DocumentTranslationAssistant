import json
import os

from agenthub.adapters import (
    AgentResponse,
    CommandAgentAdapter,
    DeepSeekAgentAdapter,
    MockAgentAdapter,
    PlanResult,
    _cli_reported_error,
)
from agenthub.config import Settings
from agenthub.models import Artifact, ConversationCreate
from agenthub.orchestrator import Orchestrator
from agenthub.store import Store


class FakeRegistry:
    def __init__(self):
        self.adapter = MockAgentAdapter()

    def for_agent(self, agent):
        return self.adapter


class ArtifactRegistry:
    deepseek = None

    def for_agent(self, agent):
        return ArtifactAdapter()


class ArtifactAdapter:
    def send_message(self, context, agent, user_prompt):
        return AgentResponse(
            "done",
            [Artifact(id="art-test", type="diff", title="Patch", language="diff", content="+ok")],
        )


class StreamingAdapter:
    def stream_message(self, context, agent, user_prompt, workdir, on_chunk):
        assert workdir.name.startswith("conv-")
        assert workdir.parent.name == "projects"
        on_chunk("# 标题\n\n")
        on_chunk("- 第一项\n")
        on_chunk("- 第二项")
        return AgentResponse("# 标题\n\n- 第一项\n- 第二项")


class StreamingRegistry:
    deepseek = None

    def for_agent(self, agent):
        return StreamingAdapter()


def test_orchestrator_dispatches_all_mentions(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    agents = store.list_enabled_agents()
    conversation = store.create_conversation(
        ConversationCreate(title="test", mode="group", agent_ids=[agent.id for agent in agents])
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@all 请拆解并实现一个网页预览",
    )

    replies = Orchestrator(store, FakeRegistry()).dispatch(conversation.id, user_message)

    assert len(replies) == 3
    assert replies[0].sender_name == "System Agent（主Agent）"
    assert {reply.sender_name for reply in replies[1:]} == {"Claude Code", "Codex"}


def test_artifact_status_update(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.list_conversations()[0]
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@Codex 实现 diff",
    )
    replies = Orchestrator(store, ArtifactRegistry()).dispatch(conversation.id, user_message)
    agent_reply = next(reply for reply in replies if reply.role == "agent")
    artifact = agent_reply.artifacts[0]

    updated = store.update_artifact_status(conversation.id, agent_reply.id, artifact.id, "accepted")

    assert updated is not None
    assert updated.artifacts[0].status == "accepted"


def test_streaming_updates_one_message_and_uses_conversation_project(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    agent = store.list_enabled_agents()[0]
    conversation = store.create_conversation(
        ConversationCreate(title="stream", mode="single", agent_ids=[agent.id])
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="流式回答",
    )

    replies = Orchestrator(store, StreamingRegistry()).dispatch(conversation.id, user_message)
    messages = store.list_messages(conversation.id)
    agent_messages = [message for message in messages if message.role == "agent"]

    assert len(replies) == 1
    assert len(agent_messages) == 1
    assert agent_messages[0].content == "# 标题\n\n- 第一项\n- 第二项"
    assert agent_messages[0].streaming is False
    assert (tmp_path / "projects" / conversation.id).is_dir()


class FinalOnlyAdapter:
    def send_message(self, context, agent, user_prompt):
        return AgentResponse("这是一个需要被拆成多个短片段逐步展示的完整回复内容。")


class FinalOnlyRegistry:
    def for_agent(self, agent):
        return FinalOnlyAdapter()


class TrackingStore(Store):
    def __init__(self, db_path):
        self.stream_lengths = []
        super().__init__(db_path)

    def update_message_content(self, message_id, content, **kwargs):
        if kwargs.get("streaming"):
            self.stream_lengths.append(len(content))
        return super().update_message_content(message_id, content, **kwargs)


def test_final_only_adapter_is_progressively_written_to_one_message(tmp_path):
    store = TrackingStore(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(title="progressive", mode="single", agent_ids=["codex"])
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="逐步回答",
    )

    replies = Orchestrator(store, FinalOnlyRegistry()).dispatch(
        conversation.id,
        user_message,
    )

    assert len(replies) == 1
    assert len(store.stream_lengths) >= 3
    assert store.stream_lengths == sorted(set(store.stream_lengths))
    assert replies[0].content == "这是一个需要被拆成多个短片段逐步展示的完整回复内容。"


def test_cli_stream_events_extract_markdown_text(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    claude, codex = store.get_agents(["claude-code", "codex"])
    adapter = CommandAgentAdapter()
    claude_event = json.dumps(
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "## Claude\n"}},
        }
    )
    codex_event = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "## Codex\n"},
        }
    )

    assert adapter._stream_chunk(claude, claude_event) == "## Claude\n"
    assert adapter._stream_chunk(codex, codex_event) == "## Codex\n"
    assert (
        adapter._stream_chunk(
            claude,
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "delta": {"type": "thinking_delta", "thinking": "internal"}
                    },
                }
            ),
        )
        == ""
    )
    assert (
        adapter._stream_chunk(
            claude,
            json.dumps({"type": "system", "subtype": "init", "tools": ["Bash"]}),
        )
        == ""
    )


def test_cli_prompts_are_read_from_stdin_instead_of_command_arguments(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    claude, codex = store.get_agents(["claude-code", "codex"])
    adapter = CommandAgentAdapter()

    claude_command = adapter._command(claude, tmp_path)
    codex_command = adapter._command(codex, tmp_path)

    assert all("完整会话历史" not in argument for argument in claude_command)
    assert all("完整会话历史" not in argument for argument in codex_command)
    assert codex_command[-1] == "-"


def test_codex_accepts_history_larger_than_command_line_limit_via_stdin(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({'type': 'item.completed', 'item': "
        "{'type': 'agent_message', 'text': f'received:{len(prompt)}'}}))\n",
        encoding="utf-8",
    )
    os.chmod(executable, 0o755)
    settings = Settings(
        workspace=tmp_path,
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
        agent_timeout_seconds=10,
        claude_cli_path="",
        codex_cli_path=str(executable),
    )
    store = Store(tmp_path / "test.sqlite3")
    codex = store.get_agents(["codex"])[0]
    conversation = store.create_conversation(
        ConversationCreate(title="large-context", mode="single", agent_ids=["codex"])
    )
    huge_history = [
        store.create_message(
            conversation.id,
            role="user",
            sender_id="user",
            sender_name="You",
            content=f"{index}:" + ("x" * 20_000),
        )
        for index in range(40)
    ]

    response = CommandAgentAdapter(settings).stream_message(
        huge_history,
        codex,
        "继续执行",
        tmp_path / "projects" / conversation.id,
        lambda chunk: None,
    )

    assert response.error is None
    assert response.content.startswith("received:")


def test_claude_reported_error_is_extracted_without_transport_log(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    claude = store.get_agents(["claude-code"])[0]
    raw_output = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "tools": ["Bash"]}),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": "private reasoning",
                        }
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "API Error: 402 Insufficient Balance",
                }
            ),
        ]
    )

    detail = _cli_reported_error(claude, raw_output)

    assert detail == "API Error: 402 Insufficient Balance"
    assert "thinking_delta" not in detail
    assert "tools" not in detail


def test_deepseek_prompt_forbids_fake_shell_and_declares_archive_directory(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    system_agent = store.get_agents(["system-agent"])[0]
    adapter = DeepSeekAgentAdapter()

    messages = adapter._messages([], system_agent, "整理需求", tmp_path)

    assert "没有 Bash、终端或文件系统工具" in messages[0]["content"]
    assert "不得输出 <bash>" in messages[0]["content"]
    assert str(tmp_path) in messages[0]["content"]


class PlannedDeepSeek:
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        assert primary_agent.is_primary
        return PlanResult(
            plan={
                "summary": "先设计再实现",
                "tasks": [
                    {"agent_id": "claude-code", "instruction": "输出架构方案"},
                    {"agent_id": "codex", "instruction": "根据方案实现并测试"},
                ],
            }
        )


class PlannedRegistry(FakeRegistry):
    def __init__(self):
        self.adapter = PlannedDeepSeekAdapter()


class PlannedDeepSeekAdapter(MockAgentAdapter):
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return PlannedDeepSeek().plan(context, agents, user_prompt, primary_agent, workdir)


def test_deepseek_plan_controls_agent_assignments(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="planned",
            mode="group",
            agent_ids=["system-agent", "claude-code", "codex"],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="开发一个聊天软件",
    )

    replies = Orchestrator(store, PlannedRegistry()).dispatch(conversation.id, user_message)

    assert replies[0].role == "orchestrator"
    assert "当前主 Agent 已理解需求" in replies[0].content
    assert [reply.sender_name for reply in replies[1:]] == ["Claude Code", "Codex"]
    assert "输出架构方案" in replies[1].content
    assert "根据方案实现并测试" in replies[2].content


class DuplicateAllPlanAdapter(MockAgentAdapter):
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return PlanResult(
            plan={
                "tasks": [
                    {"agent_id": "codex", "instruction": "实现页面"},
                    {"agent_id": "codex", "instruction": "运行测试"},
                ]
            }
        )


class DuplicateAllRegistry:
    def __init__(self):
        self.adapter = DuplicateAllPlanAdapter()

    def for_agent(self, agent):
        return self.adapter


def test_all_mentions_cover_every_non_primary_agent_and_merge_duplicates(tmp_path):
    from agenthub.models import AgentCreate

    store = Store(tmp_path / "test.sqlite3")
    researcher = store.create_agent(
        AgentCreate(
            name="验收研究员",
            kind="api",
            provider="deepseek",
            description="负责验收研究和质量检查。",
            system_prompt="先研究需求，再输出验收结论。",
        )
    )
    conversation = store.create_conversation(
        ConversationCreate(
            title="all-members",
            mode="group",
            agent_ids=["system-agent", "claude-code", "codex", researcher.id],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@all 实现一个待办应用",
    )

    replies = Orchestrator(store, DuplicateAllRegistry()).dispatch(
        conversation.id,
        user_message,
    )

    agent_replies = [reply for reply in replies if reply.role == "agent"]
    assert [reply.sender_name for reply in agent_replies] == [
        "Codex",
        "Claude Code",
        "验收研究员",
    ]
    assert agent_replies[0].content.count("Codex 测试适配器收到") == 1
    assert "实现页面" in agent_replies[0].content
    assert "运行测试" in agent_replies[0].content


class FileTaskPlanAdapter(MockAgentAdapter):
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return PlanResult(
            plan={
                "tasks": [
                    {
                        "agent_id": "system-agent",
                        "instruction": "创建代码文件并运行测试",
                    }
                ]
            }
        )


class FileTaskRegistry:
    def __init__(self):
        self.adapter = FileTaskPlanAdapter()

    def for_agent(self, agent):
        return self.adapter


def test_file_task_assigned_to_api_agent_is_routed_to_cli_agent(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="file-routing",
            mode="group",
            agent_ids=["system-agent", "codex"],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="实现一个页面",
    )

    replies = Orchestrator(store, FileTaskRegistry()).dispatch(
        conversation.id,
        user_message,
    )

    assert "@Codex：创建代码文件并运行测试" in replies[0].content
    assert replies[1].sender_name == "Codex"


class ApiOutputAdapter(MockAgentAdapter):
    def send_message(self, context, agent, user_prompt):
        return AgentResponse("# 研究结论\n\n保留最小范围。")


class ApiOutputRegistry:
    def for_agent(self, agent):
        return ApiOutputAdapter()


def test_api_agent_text_output_is_archived_in_conversation_project(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="api-output",
            mode="single",
            agent_ids=["system-agent"],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="整理研究结论",
    )

    replies = Orchestrator(store, ApiOutputRegistry()).dispatch(
        conversation.id,
        user_message,
    )

    output_files = list(
        (tmp_path / "projects" / conversation.id / "agent-outputs").glob("*.md")
    )
    assert replies[0].sender_name == "System Agent"
    assert len(output_files) == 1
    assert output_files[0].read_text(encoding="utf-8") == replies[0].content


class FailedDeepSeek:
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return PlanResult(error="DeepSeek 调用失败，HTTP 401：认证失败")


class FailedPlanRegistry(FakeRegistry):
    def __init__(self):
        self.adapter = FailedPlanAdapter()


class FailedPlanAdapter(MockAgentAdapter):
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return FailedDeepSeek().plan(context, agents, user_prompt, primary_agent, workdir)


def test_orchestrator_exposes_deepseek_failure_reason(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.list_conversations()[0]
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@all 实现任务",
    )

    replies = Orchestrator(store, FailedPlanRegistry()).dispatch(conversation.id, user_message)

    assert "HTTP 401" in replies[0].content
    assert "已按本地规则降级" in replies[0].content


class TrackingPlanAdapter(MockAgentAdapter):
    def __init__(self, provider, calls):
        self.provider = provider
        self.calls = calls

    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        self.calls.append((self.provider, primary_agent.id))
        return PlanResult(
            plan={
                "summary": "由当前主 Agent 规划",
                "tasks": [{"agent_id": "codex", "instruction": "执行验证任务"}],
            }
        )


class TrackingPlanRegistry:
    def __init__(self):
        self.calls = []

    def for_agent(self, agent):
        return TrackingPlanAdapter(agent.provider, self.calls)


def test_switched_primary_uses_its_own_planning_adapter(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="switched-primary",
            mode="group",
            agent_ids=["system-agent", "claude-code", "codex"],
        )
    )
    store.update_conversation_agent(conversation.id, "claude-code", is_primary=True)
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@all 执行验证任务",
    )
    registry = TrackingPlanRegistry()

    replies = Orchestrator(store, registry).dispatch(conversation.id, user_message)

    assert registry.calls[0] == ("claude-code", "claude-code")
    assert replies[0].sender_name == "Claude Code（主Agent）"
    assert replies[1].sender_name == "Codex"


def test_custom_agent_contact_scope_and_conversation_management(tmp_path):
    from agenthub.models import AgentCreate

    store = Store(tmp_path / "test.sqlite3")
    agent = store.create_agent(
        AgentCreate(
            name="临时研究员",
            kind="api",
            provider="deepseek",
            system_prompt="先核对事实。",
            description="负责研究和事实核查。",
            in_contacts=False,
        )
    )
    conversation = store.list_conversations()[0]

    updated = store.update_conversation(
        conversation.id,
        title="产品研发群",
        agent_ids=[*conversation.agent_ids, agent.id],
    )

    assert agent.id not in {item.id for item in store.list_contact_agents()}
    assert updated is not None
    assert updated.title == "产品研发群"
    assert agent.id in updated.agent_ids
    assert store.delete_conversation(conversation.id)
    assert store.get_conversation(conversation.id) is None


def test_deploy_api_and_download(tmp_path):
    from fastapi.testclient import TestClient
    from agenthub.main import app, store as app_store
    import agenthub.main

    test_db = tmp_path / "test_main.sqlite3"
    old_store = app_store

    temp_store = Store(test_db)
    agenthub.main.store = temp_store

    client = TestClient(app)

    conversation = temp_store.create_conversation(
        ConversationCreate(title="Test Deploy", mode="single", agent_ids=["claude-code"])
    )

    response = client.post(
        f"/api/conversations/{conversation.id}/messages",
        json={"content": "部署当前项目"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_message"]["content"] == "部署当前项目"

    # Test ZIP download API
    import shutil
    registry = agenthub.main.AdapterRegistry()
    proj_dir = registry.settings.workspace / "projects" / conversation.id
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")

    download_res = client.get(f"/api/conversations/{conversation.id}/download")
    assert download_res.status_code == 200
    assert download_res.headers["content-type"] == "application/zip"

    # Restore store
    agenthub.main.store = old_store
    shutil.rmtree(proj_dir, ignore_errors=True)


def test_deploy_uses_configured_workspace_for_preview(tmp_path, monkeypatch):
    import asyncio
    import json
    from agenthub.deployer import dispatch_deploy

    workspace = tmp_path / "workspace"
    backend_dir = tmp_path / "backend"
    workspace.mkdir()
    backend_dir.mkdir()
    monkeypatch.setenv("AGENTHUB_WORKSPACE", str(workspace))

    store = Store(backend_dir / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(title="Deploy Workspace", mode="single", agent_ids=["codex"])
    )
    store.conn.close()

    asyncio.run(dispatch_deploy(backend_dir / "test.sqlite3", conversation.id))

    expected_index = workspace / "projects" / conversation.id / "index.html"
    wrong_index = backend_dir / "projects" / conversation.id / "index.html"
    assert expected_index.is_file()
    assert not wrong_index.exists()

    verify_store = Store(backend_dir / "test.sqlite3")
    deploy_message = next(
        message for message in verify_store.list_messages(conversation.id)
        if message.sender_id == "deployer"
    )
    artifact_data = json.loads(deploy_message.artifacts[0].content)
    assert artifact_data["status"] == "success"
    assert artifact_data["url"].endswith(f"/static/projects/{conversation.id}/index.html")


def test_version_manager_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHUB_WORKSPACE", str(tmp_path))
    from agenthub.version_manager import VersionManager

    conversation_id = "test_conv_versions"
    workspace_dir = tmp_path / "projects" / conversation_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Create a dummy file in workspace
    dummy_file = workspace_dir / "app.py"
    dummy_file.write_text("print('hello')", encoding="utf-8")

    # Save version
    v1 = VersionManager.save_version(conversation_id, "Initial version")
    assert v1.startswith("v1_")

    # Check version list
    versions = VersionManager.list_versions(conversation_id)
    assert len(versions) == 1
    assert versions[0]["id"] == v1
    assert versions[0]["message"] == "Initial version"

    # Modify the file and create a new one
    dummy_file.write_text("print('hello world')", encoding="utf-8")
    other_file = workspace_dir / "helper.py"
    other_file.write_text("def run(): pass", encoding="utf-8")

    v2 = VersionManager.save_version(conversation_id, "Second version")
    assert v2.startswith("v2_")

    # Revert to version 1
    VersionManager.revert_version(conversation_id, v1)

    # Check that dummy file has reverted and helper.py is gone
    assert dummy_file.read_text(encoding="utf-8") == "print('hello')"
    assert not other_file.exists()

    # Verify that a rollback version log entry was added
    versions_after = VersionManager.list_versions(conversation_id)
    assert len(versions_after) == 3
    assert "Rollback to" in versions_after[0]["message"]


def test_parallel_dispatch_and_merger(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHUB_WORKSPACE", str(tmp_path))
    store = Store(tmp_path / "test.sqlite3")

    # Enable deepseek to orchestrate
    class TestPlannedDeepSeek:
        def plan(self, context, agents, user_prompt):
            return PlanResult(
                plan={
                    "summary": "Parallel plan",
                    "tasks": [
                        {"agent_id": "claude-code", "instruction": "Do job 1"},
                        {"agent_id": "codex", "instruction": "Do job 2"},
                    ],
                }
            )

    class TestParallelRegistry:
        def for_agent(self, agent):
            if agent.id == "system-agent":
                return TestPlannedDeepSeek()
            class NonConflictingAdapter:
                def stream_message(self, context, agent, user_prompt, workdir, on_chunk):
                    workdir.mkdir(parents=True, exist_ok=True)
                    if agent.id == "claude-code":
                        (workdir / "file1.py").write_text("content1", encoding="utf-8")
                    elif agent.id == "codex":
                        (workdir / "file2.py").write_text("content2", encoding="utf-8")
                    on_chunk(f"{agent.name} done")
                    return AgentResponse(f"{agent.name} completed successfully.")
            return NonConflictingAdapter()

    # Create group conversation
    agents = store.list_enabled_agents()
    conversation = store.create_conversation(
        ConversationCreate(title="Parallel Merge", mode="group", agent_ids=[a.id for a in agents])
    )

    # Initialize workspace project dir with a base file
    proj_dir = tmp_path / "projects" / conversation.id
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "base.py").write_text("print('base')", encoding="utf-8")

    # Send a message to trigger orchestrator dispatch
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="并行修改代码",
    )

    orchestrator = Orchestrator(store, TestParallelRegistry())
    replies = orchestrator.dispatch(conversation.id, user_message, parallel=True)

    assert len(replies) == 3
    assert replies[0].role == "orchestrator"
    assert replies[1].role == "agent"
    assert replies[2].role == "agent"

    # Verify that files merged cleanly
    assert (proj_dir / "base.py").exists()
    assert (proj_dir / "file1.py").exists()
    assert (proj_dir / "file2.py").exists()
    assert (proj_dir / "file1.py").read_text(encoding="utf-8") == "content1"
    assert (proj_dir / "file2.py").read_text(encoding="utf-8") == "content2"

    # Verify snapshot list includes parallel completion
    from agenthub.version_manager import VersionManager
    versions = VersionManager.list_versions(conversation.id)
    assert len(versions) >= 2
    assert "Parallel completion by" in versions[0]["message"]
    assert "Before parallel dispatch" in versions[1]["message"]


def test_parallel_dispatch_conflict_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHUB_WORKSPACE", str(tmp_path))
    store = Store(tmp_path / "test.sqlite3")

    class TestPlannedDeepSeek:
        def plan(self, context, agents, user_prompt):
            return PlanResult(
                plan={
                    "summary": "Parallel conflict plan",
                    "tasks": [
                        {"agent_id": "claude-code", "instruction": "Modify code"},
                        {"agent_id": "codex", "instruction": "Modify code"},
                    ],
                }
            )

    class TestConflictRegistry:
        def for_agent(self, agent):
            if agent.id == "system-agent":
                return TestPlannedDeepSeek()
            class ConflictingAdapter:
                def stream_message(self, context, agent, user_prompt, workdir, on_chunk):
                    workdir.mkdir(parents=True, exist_ok=True)
                    if agent.id == "claude-code":
                        (workdir / "conflict.py").write_text("Hello from Claude", encoding="utf-8")
                    elif agent.id == "codex":
                        (workdir / "conflict.py").write_text("Hello from Codex", encoding="utf-8")
                    on_chunk(f"{agent.name} wrote conflict")
                    return AgentResponse(f"{agent.name} done")
            return ConflictingAdapter()

    # Create group conversation
    agents = store.list_enabled_agents()
    conversation = store.create_conversation(
        ConversationCreate(title="Parallel Conflict", mode="group", agent_ids=[a.id for a in agents])
    )

    proj_dir = tmp_path / "projects" / conversation.id
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "conflict.py").write_text("Original base", encoding="utf-8")

    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="并行修改同一文件",
    )

    orchestrator = Orchestrator(store, TestConflictRegistry())
    replies = orchestrator.dispatch(conversation.id, user_message, parallel=True)

    assert len(replies) == 4
    assert replies[3].role == "orchestrator"
    assert "检测到并行代码合并冲突" in replies[3].content

    conflict_art = replies[3].artifacts[0]
    assert conflict_art.type == "conflict"

    conflicts_data = json.loads(conflict_art.content)
    assert len(conflicts_data) == 1
    assert conflicts_data[0]["file"] == "conflict.py"
    assert conflicts_data[0]["agent_a"] == "Hello from Claude"
    assert conflicts_data[0]["agent_b"] == "Hello from Codex"
    assert conflicts_data[0]["base"] == "Original base"

    # Verify file content in workspace has conflict markers
    content_with_markers = (proj_dir / "conflict.py").read_text(encoding="utf-8")
    assert "<<<<<<< Claude Code" in content_with_markers
    assert "=======" in content_with_markers
    assert ">>>>>>> Codex" in content_with_markers


def test_default_contacts_have_role_and_system_prompt(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    contacts = {agent.name: agent for agent in store.list_contact_agents()}

    assert {"System Agent", "Claude Code", "Codex"} <= set(contacts)
    for name in {"System Agent", "Claude Code", "Codex"}:
        assert contacts[name].description.strip()
        assert contacts[name].system_prompt.strip()


def test_conversation_member_overrides_do_not_change_contact_defaults(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="overrides",
            mode="group",
            agent_ids=["system-agent", "codex"],
        )
    )
    original = store.get_agent_by_name("Codex")
    assert original is not None

    updated = store.update_conversation_agent(
        conversation.id,
        "codex",
        name="实现工程师",
        description="只负责当前群聊的实现。",
        system_prompt="先阅读历史，再完成实现。",
    )

    assert updated is not None
    assert updated.name == "实现工程师"
    assert store.get_agent_by_name("Codex") == original


def test_switching_primary_swaps_role_and_prompt_in_conversation(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="primary",
            mode="group",
            agent_ids=["system-agent", "codex"],
        )
    )
    before = {member.id: member for member in store.list_conversation_agents(conversation.id)}

    store.update_conversation_agent(conversation.id, "codex", is_primary=True)
    after = {member.id: member for member in store.list_conversation_agents(conversation.id)}

    assert after["codex"].is_primary is True
    assert after["system-agent"].is_primary is False
    assert after["codex"].description == before["system-agent"].description
    assert after["codex"].system_prompt == before["system-agent"].system_prompt
    assert after["system-agent"].description == before["codex"].description
    assert after["system-agent"].system_prompt == before["codex"].system_prompt


class SequentialAdapter:
    def __init__(self):
        self.contexts = []

    def send_message(self, context, agent, user_prompt):
        self.contexts.append(list(context))
        return AgentResponse(f"{agent.name} completed")


class SequentialRegistry:
    deepseek = None

    def __init__(self):
        self.adapter = SequentialAdapter()

    def for_agent(self, agent):
        return self.adapter


def test_agents_execute_in_order_with_previous_reply_in_context(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="sequential",
            mode="group",
            agent_ids=["system-agent", "claude-code", "codex"],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="@all 顺序执行",
    )
    registry = SequentialRegistry()

    Orchestrator(store, registry).dispatch(conversation.id, user_message)

    assert len(registry.adapter.contexts) == 2
    assert all(message.content != "Claude Code completed" for message in registry.adapter.contexts[0])
    assert any(message.content == "Claude Code completed" for message in registry.adapter.contexts[1])


def test_only_custom_contacts_can_be_removed_without_losing_conversation_members(tmp_path):
    from agenthub.models import AgentCreate

    store = Store(tmp_path / "test.sqlite3")
    custom = store.create_agent(
        AgentCreate(
            name="可删除研究员",
            kind="api",
            provider="deepseek",
            description="负责临时研究。",
            system_prompt="先研究再回答。",
        )
    )
    conversation = store.create_conversation(
        ConversationCreate(title="keep history", mode="single", agent_ids=[custom.id])
    )

    assert store.remove_contact("system-agent") is False
    assert store.remove_contact("claude-code") is False
    assert store.remove_contact("codex") is False
    assert store.remove_contact(custom.id) is True
    assert custom.id not in {agent.id for agent in store.list_contact_agents()}
    assert [member.id for member in store.list_conversation_agents(conversation.id)] == [custom.id]


class FailingAdapter(MockAgentAdapter):
    def plan(self, context, agents, user_prompt, primary_agent, workdir):
        return PlanResult(
            plan={"tasks": [{"agent_id": "system-agent", "instruction": "执行任务"}]}
        )

    def send_message(self, context, agent, user_prompt):
        if agent.id == "system-agent":
            return AgentResponse("DeepSeek 余额不足", error="HTTP 402")
        return AgentResponse(f"{agent.name} 接管并完成")


class FailingRegistry:
    def __init__(self):
        self.adapter = FailingAdapter()

    def for_agent(self, agent):
        return self.adapter


def test_failed_agent_task_is_transferred_to_another_member(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    conversation = store.create_conversation(
        ConversationCreate(
            title="failover",
            mode="group",
            agent_ids=["system-agent", "codex"],
        )
    )
    user_message = store.create_message(
        conversation.id,
        role="user",
        sender_id="user",
        sender_name="You",
        content="完成任务",
    )

    replies = Orchestrator(store, FailingRegistry()).dispatch(conversation.id, user_message)

    assert "自动转交给 Codex" in replies[1].content
    assert replies[2].sender_name == "Codex"
    assert replies[2].content == "Codex 接管并完成"
