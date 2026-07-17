from __future__ import annotations

import inspect
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from .adapters import AdapterRegistry, AgentResponse, PlanResult
from .merger import FileMerger
from .models import Agent, Artifact, Message
from .store import Store
from .version_manager import VersionManager


class Orchestrator:
    def __init__(self, store: Store, registry: AdapterRegistry | None = None):
        self.store = store
        self.registry = registry or AdapterRegistry()

    def dispatch(
        self,
        conversation_id: str,
        user_message: Message,
        parallel: bool = False,
    ) -> list[Message]:
        conversation = self.store.get_conversation(conversation_id)
        if not conversation:
            return []

        agents = [
            agent
            for agent in self.store.list_conversation_agents(conversation_id)
            if agent.enabled
        ]
        if not agents:
            return []

        history = self.store.list_messages(conversation_id)
        project_dir = self._project_dir(conversation_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        primary = next((agent for agent in agents if agent.is_primary), agents[0])

        planning: PlanResult | None = None
        if conversation.mode == "group":
            planning = self._plan(
                primary,
                history,
                agents,
                user_message.content,
                project_dir,
            )

        plan = planning.plan if planning else None
        planning_error = planning.error if planning else "主 Agent 规划器未配置。"
        assignments = (
            [(agents[0], user_message.content)]
            if conversation.mode == "single"
            else self._assignments(plan, user_message.content, agents, primary)
        )

        replies: list[Message] = []
        if conversation.mode == "group":
            summary = self.store.create_message(
                conversation_id,
                role="orchestrator",
                sender_id=primary.id,
                sender_name=f"{primary.name}（主Agent）",
                content=self._summary(
                    user_message.content,
                    assignments,
                    project_dir,
                    plan is not None,
                    planning_error,
                ),
            )
            replies.append(summary)
            history.append(summary)

        if parallel and len(assignments) > 1:
            replies.extend(
                self._execute_parallel(
                    conversation_id,
                    history,
                    assignments,
                    agents,
                    primary,
                    project_dir,
                    user_message.content,
                )
            )
        else:
            replies.extend(
                self._execute_sequential(
                    conversation_id,
                    history,
                    assignments,
                    agents,
                    primary,
                    project_dir,
                )
            )
        return replies

    def _plan(
        self,
        primary: Agent,
        history: list[Message],
        agents: list[Agent],
        prompt: str,
        project_dir: Path,
    ) -> PlanResult:
        adapter = self.registry.for_agent(primary)
        plan_method = getattr(adapter, "plan", None)
        if not plan_method:
            return PlanResult(error=f"{primary.name} 当前不支持任务规划。")
        try:
            parameter_count = len(inspect.signature(plan_method).parameters)
            if parameter_count >= 5:
                return plan_method(history, agents, prompt, primary, project_dir)
            return plan_method(history, agents, prompt)
        except Exception as exc:
            return PlanResult(error=f"{primary.name} 规划失败：{exc}")

    def _execute_sequential(
        self,
        conversation_id: str,
        history: list[Message],
        assignments: list[tuple[Agent, str]],
        agents: list[Agent],
        primary: Agent,
        project_dir: Path,
    ) -> list[Message]:
        replies: list[Message] = []
        failed_agent_ids: set[str] = set()
        for agent, instruction in assignments:
            message, response = self._execute_agent(
                conversation_id,
                history,
                agent,
                instruction,
                project_dir,
            )
            replies.append(message)
            history.append(message)
            if not response.error:
                continue

            failed_agent_ids.add(agent.id)
            fallback = self._fallback_agent(
                agents,
                failed_agent_ids,
                preferred_primary=primary,
            )
            if not fallback:
                continue

            transfer_notice = self.store.update_message_content(
                message.id,
                f"{response.content}\n\n任务已自动转交给 {fallback.name} 继续执行。",
                artifacts=response.artifacts,
                streaming=False,
            )
            if transfer_notice:
                replies[-1] = transfer_notice
                history[-1] = transfer_notice

            fallback_message, fallback_response = self._execute_agent(
                conversation_id,
                history,
                fallback,
                instruction,
                project_dir,
            )
            replies.append(fallback_message)
            history.append(fallback_message)
            if fallback_response.error:
                failed_agent_ids.add(fallback.id)
        return replies

    def _execute_parallel(
        self,
        conversation_id: str,
        history: list[Message],
        assignments: list[tuple[Agent, str]],
        agents: list[Agent],
        primary: Agent,
        project_dir: Path,
        original_prompt: str,
    ) -> list[Message]:
        VersionManager.save_version(
            conversation_id,
            f"Before parallel dispatch: {original_prompt}",
        )
        base_dir = project_dir.parent / f"{conversation_id}_tmp_base"
        shutil.rmtree(base_dir, ignore_errors=True)
        shutil.copytree(project_dir, base_dir, dirs_exist_ok=True)

        replies: list[Message] = []
        agent_dirs: dict[str, tuple[str, Path]] = {}
        threads: list[tuple[Agent, str, threading.Thread]] = []
        responses: dict[str, AgentResponse] = {}
        lock = threading.Lock()

        for index, (agent, instruction) in enumerate(assignments):
            message = self.store.create_message(
                conversation_id,
                role="agent",
                sender_id=agent.id,
                sender_name=agent.name,
                content="",
                streaming=True,
            )
            replies.append(message)
            temp_key = f"{agent.id}-{index}"
            temp_dir = project_dir.parent / f"{conversation_id}_tmp_{temp_key}"
            shutil.rmtree(temp_dir, ignore_errors=True)
            shutil.copytree(project_dir, temp_dir, dirs_exist_ok=True)
            agent_dirs[temp_key] = (agent.name, temp_dir)

            def worker(
                target_agent: Agent = agent,
                target_instruction: str = instruction,
                message_id: str = message.id,
                workdir: Path = temp_dir,
            ) -> None:
                streamed_content = ""

                def on_chunk(chunk: str) -> None:
                    nonlocal streamed_content
                    for fragment in self._stream_fragments(chunk):
                        streamed_content += fragment
                        self.store.update_message_content(
                            message_id,
                            streamed_content,
                            streaming=True,
                        )
                        if len(chunk) > 24:
                            time.sleep(0.015)

                try:
                    response = self._send(
                        history,
                        target_agent,
                        target_instruction,
                        workdir,
                        on_chunk,
                    )
                except Exception as exc:
                    content = f"{target_agent.name} 执行时发生异常：{exc}"
                    on_chunk(content)
                    response = AgentResponse(content, error=content)
                if target_agent.kind == "api" and response.content:
                    self._persist_api_output(
                        workdir,
                        target_agent,
                        message_id,
                        response.content,
                    )
                with lock:
                    responses[message_id] = response

            thread = threading.Thread(target=worker, daemon=True)
            threads.append((agent, message.id, thread))

        for _, _, thread in threads:
            thread.start()
        for _, _, thread in threads:
            thread.join()

        try:
            conflicts = FileMerger.merge_parallel_changes(
                base_dir,
                project_dir,
                agent_dirs,
            )
        finally:
            shutil.rmtree(base_dir, ignore_errors=True)
            for _, temp_dir in agent_dirs.values():
                shutil.rmtree(temp_dir, ignore_errors=True)

        failed_agent_ids: set[str] = set()
        for index, (agent, message_id, _) in enumerate(threads):
            response = responses.get(
                message_id,
                AgentResponse(
                    f"{agent.name} 未返回执行结果。",
                    error="Agent 未返回执行结果",
                ),
            )
            final_message = self.store.update_message_content(
                message_id,
                response.content,
                artifacts=response.artifacts,
                streaming=False,
            )
            if final_message:
                replies[index] = final_message
            if response.error:
                failed_agent_ids.add(agent.id)

        if conflicts:
            conflict_artifact = Artifact(
                id=f"art-{uuid.uuid4().hex[:8]}",
                type="conflict",
                title="并行代码合并冲突",
                content=json.dumps(conflicts, ensure_ascii=False),
            )
            replies.append(
                self.store.create_message(
                    conversation_id,
                    role="orchestrator",
                    sender_id=primary.id,
                    sender_name=f"{primary.name}（主Agent）",
                    content="检测到并行代码合并冲突，请处理冲突后再继续。",
                    artifacts=[conflict_artifact],
                )
            )
        else:
            VersionManager.save_version(
                conversation_id,
                "Parallel completion by: "
                + ", ".join(agent.name for agent, _ in assignments),
            )

        # Parallel workers cannot safely share each other's partial context. Retry failed
        # tasks sequentially after the merge so another available member can take over.
        retry_history = [*history, *replies]
        for agent, instruction in assignments:
            if agent.id not in failed_agent_ids:
                continue
            fallback = self._fallback_agent(
                agents,
                failed_agent_ids,
                preferred_primary=primary,
            )
            if not fallback:
                continue
            failed_message = next(
                (message for message in replies if message.sender_id == agent.id),
                None,
            )
            if failed_message:
                updated = self.store.update_message_content(
                    failed_message.id,
                    f"{failed_message.content}\n\n任务已自动转交给 {fallback.name} 继续执行。",
                    artifacts=failed_message.artifacts,
                    streaming=False,
                )
                if updated:
                    replies[replies.index(failed_message)] = updated
                    retry_history.append(updated)
            fallback_message, fallback_response = self._execute_agent(
                conversation_id,
                retry_history,
                fallback,
                instruction,
                project_dir,
            )
            replies.append(fallback_message)
            retry_history.append(fallback_message)
            if fallback_response.error:
                failed_agent_ids.add(fallback.id)
        return replies

    def _execute_agent(
        self,
        conversation_id: str,
        history: list[Message],
        agent: Agent,
        instruction: str,
        project_dir: Path,
    ) -> tuple[Message, AgentResponse]:
        message = self.store.create_message(
            conversation_id,
            role="agent",
            sender_id=agent.id,
            sender_name=agent.name,
            content="",
            streaming=True,
        )
        streamed_content = ""

        def on_chunk(chunk: str) -> None:
            nonlocal streamed_content
            for fragment in self._stream_fragments(chunk):
                streamed_content += fragment
                self.store.update_message_content(
                    message.id,
                    streamed_content,
                    streaming=True,
                )
                if len(chunk) > 24:
                    time.sleep(0.015)

        try:
            response = self._send(
                history,
                agent,
                instruction,
                project_dir,
                on_chunk,
            )
        except Exception as exc:
            content = f"{agent.name} 执行时发生异常：{exc}"
            on_chunk(content)
            response = AgentResponse(content, error=content)

        final_content = response.content or streamed_content
        final_message = self.store.update_message_content(
            message.id,
            final_content,
            artifacts=response.artifacts,
            streaming=False,
        )
        if agent.kind == "api" and final_content:
            self._persist_api_output(
                project_dir,
                agent,
                message.id,
                final_content,
            )
        return final_message or message, response

    def _send(
        self,
        history: list[Message],
        agent: Agent,
        instruction: str,
        project_dir: Path,
        on_chunk,
    ) -> AgentResponse:
        adapter = self.registry.for_agent(agent)
        if hasattr(adapter, "stream_message"):
            return adapter.stream_message(
                history,
                agent,
                instruction,
                project_dir,
                on_chunk,
            )
        response = adapter.send_message(history, agent, instruction)
        if response.content:
            on_chunk(response.content)
        return response

    def _project_dir(self, conversation_id: str) -> Path:
        settings = getattr(self.registry, "settings", None)
        workspace = getattr(settings, "workspace", self.store.db_path.parent)
        return Path(workspace) / "projects" / conversation_id

    def _assignments(
        self,
        plan: dict[str, object] | None,
        prompt: str,
        agents: list[Agent],
        primary: Agent | None,
    ) -> list[tuple[Agent, str]]:
        by_id = {agent.id: agent for agent in agents}
        if plan and isinstance(plan.get("tasks"), list):
            assignments: list[tuple[Agent, str]] = []
            for task in plan["tasks"]:
                if not isinstance(task, dict):
                    continue
                agent = by_id.get(str(task.get("agent_id", "")))
                instruction = str(task.get("instruction", "")).strip()
                if agent and instruction:
                    assignments.append((agent, instruction))
            if assignments:
                return self._normalize_assignments(
                    self._route_file_tasks(assignments, agents),
                    prompt,
                    agents,
                    primary,
                )
        assignments = [
            (agent, prompt) for agent in self._select_agents(prompt, agents, primary)
        ]
        return self._normalize_assignments(
            self._route_file_tasks(assignments, agents),
            prompt,
            agents,
            primary,
        )

    def _normalize_assignments(
        self,
        assignments: list[tuple[Agent, str]],
        prompt: str,
        agents: list[Agent],
        primary: Agent | None,
    ) -> list[tuple[Agent, str]]:
        by_agent: dict[str, tuple[Agent, list[str]]] = {}
        order: list[str] = []
        for agent, instruction in assignments:
            if agent.id not in by_agent:
                by_agent[agent.id] = (agent, [])
                order.append(agent.id)
            instructions = by_agent[agent.id][1]
            if instruction not in instructions:
                instructions.append(instruction)

        if self._targets_all(prompt):
            targets = [agent for agent in agents if not primary or agent.id != primary.id]
            if not targets and primary:
                targets = [primary]
            for agent in targets:
                if agent.id in by_agent:
                    continue
                by_agent[agent.id] = (
                    agent,
                    [
                        "请依据你在当前会话中的职责完成本轮协作任务："
                        f"{agent.description or '分析需求并提供专业产出'}。"
                        f"结合完整历史和其他 Agent 的输出处理原始需求：{prompt}"
                    ],
                )
                order.append(agent.id)

        return [
            (by_agent[agent_id][0], "\n\n".join(by_agent[agent_id][1]))
            for agent_id in order
        ]

    def _route_file_tasks(
        self,
        assignments: list[tuple[Agent, str]],
        agents: list[Agent],
    ) -> list[tuple[Agent, str]]:
        routed: list[tuple[Agent, str]] = []
        for agent, instruction in assignments:
            if agent.kind == "cli" or not self._requires_file_tools(instruction):
                routed.append((agent, instruction))
                continue
            replacement = self._file_agent(instruction, agents)
            routed.append((replacement or agent, instruction))
        return routed

    def _requires_file_tools(self, instruction: str) -> bool:
        lowered = instruction.strip().casefold()
        analysis_prefixes = (
            "整理",
            "研究",
            "分析",
            "验收",
            "审查",
            "复核",
            "规划",
            "设计",
            "输出",
            "总结",
        )
        explicit_file_actions = (
            "创建文件",
            "新建文件",
            "修改文件",
            "写入文件",
            "运行命令",
            "运行测试",
            "执行测试",
            "构建项目",
        )
        if lowered.startswith(analysis_prefixes) and not any(
            action in lowered for action in explicit_file_actions
        ):
            return False
        return any(
            term in lowered
            for term in (
                "实现",
                "代码",
                "创建",
                "新建",
                "修改",
                "写入",
                "文件",
                "目录",
                "运行",
                "测试",
                "构建",
                "修复",
                "网页",
                "web app",
                "docs/",
                "agents.md",
            )
        )

    def _file_agent(self, instruction: str, agents: list[Agent]) -> Agent | None:
        cli_agents = [agent for agent in agents if agent.kind == "cli"]
        if not cli_agents:
            return None
        lowered = instruction.casefold()
        if any(term in lowered for term in ("审查", "架构", "方案", "复核")):
            claude = self._first_by_provider(cli_agents, "claude-code")
            if claude:
                return claude
        codex = self._first_by_provider(cli_agents, "codex")
        return codex or cli_agents[0]

    def _select_agents(
        self,
        prompt: str,
        agents: list[Agent],
        primary: Agent | None,
    ) -> list[Agent]:
        if not agents:
            return []
        prompt_lower = prompt.casefold()
        if "@all" in prompt_lower or "所有人" in prompt:
            selected = [agent for agent in agents if agent.id != primary.id]
            return selected or ([primary] if primary else agents)

        mentioned = []
        for agent in agents:
            aliases = {agent.name.casefold(), agent.id.casefold(), agent.provider.casefold()}
            if any(f"@{alias}" in prompt_lower for alias in aliases):
                mentioned.append(agent)
        if mentioned:
            return mentioned

        if any(word in prompt_lower for word in ["diff", "代码", "实现", "bug", "前端"]):
            codex = self._first_by_provider(agents, "codex")
            return [codex] if codex else [agents[0]]
        if any(word in prompt_lower for word in ["架构", "设计", "拆解", "方案"]):
            claude = self._first_by_provider(agents, "claude-code")
            return [claude] if claude else [agents[0]]
        return [agent for agent in agents if agent.id != primary.id][:2] or agents[:1]

    def _targets_all(self, prompt: str) -> bool:
        lowered = prompt.casefold()
        return "@all" in lowered or "所有人" in prompt or "全部 agent" in lowered

    def _stream_fragments(self, chunk: str, size: int = 12) -> list[str]:
        if len(chunk) <= 24:
            return [chunk] if chunk else []
        return [chunk[index:index + size] for index in range(0, len(chunk), size)]

    def _fallback_agent(
        self,
        agents: list[Agent],
        failed_agent_ids: set[str],
        *,
        preferred_primary: Agent | None,
    ) -> Agent | None:
        candidates = [agent for agent in agents if agent.id not in failed_agent_ids]
        if not candidates:
            return None
        cli = next((agent for agent in candidates if agent.kind == "cli"), None)
        if cli:
            return cli
        if preferred_primary and preferred_primary.id not in failed_agent_ids:
            return preferred_primary
        return candidates[0]

    def _first_by_provider(self, agents: list[Agent], provider: str) -> Agent | None:
        return next((agent for agent in agents if agent.provider == provider), None)

    def _summary(
        self,
        prompt: str,
        assignments: list[tuple[Agent, str]],
        project_dir: Path,
        planned_by_primary: bool,
        planning_error: str | None,
    ) -> str:
        if not assignments:
            return "当前会话没有可用 Agent，请先添加会话成员。"
        lines = [
            f"{index}. @{agent.name}：{instruction}"
            for index, (agent, instruction) in enumerate(assignments, start=1)
        ]
        source = (
            "当前主 Agent 已理解需求并拆解为"
            if planned_by_primary
            else f"主 Agent 规划失败（{planning_error or '未知原因'}），已按本地规则降级为"
        )
        directory_rule = (
            f"所有 Agent：代码、文本、规范和协作文档等全部产物只能放在 `{project_dir}`，"
            "不得写入父仓库。"
        )
        return (
            f"{source}以下任务，将按顺序依次执行：\n"
            + "\n".join(lines)
            + f"\n\n{directory_rule}\n\n原始需求：{prompt[:160]}"
        )

    def _persist_api_output(
        self,
        project_dir: Path,
        agent: Agent,
        message_id: str,
        content: str,
    ) -> None:
        output_dir = project_dir / "agent-outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_agent_id = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in agent.id
        )
        (output_dir / f"{safe_agent_id}-{message_id}.md").write_text(
            content,
            encoding="utf-8",
        )
