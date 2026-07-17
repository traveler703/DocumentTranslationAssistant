from __future__ import annotations

import json
import re
import select
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

import truststore

from .config import Settings, get_settings
from .models import Agent, Artifact, Message

ChunkCallback = Callable[[str], None]


class AgentResponse:
    def __init__(
        self,
        content: str,
        artifacts: list[Artifact] | None = None,
        error: str | None = None,
    ):
        self.content = content
        self.artifacts = artifacts or []
        self.error = error


class PlanResult:
    def __init__(self, plan: dict[str, object] | None = None, error: str | None = None):
        self.plan = plan
        self.error = error


class AgentAdapter(ABC):
    @abstractmethod
    def send_message(self, context: list[Message], agent: Agent, user_prompt: str) -> AgentResponse:
        raise NotImplementedError

    def stream_message(
        self,
        context: list[Message],
        agent: Agent,
        user_prompt: str,
        workdir: Path,
        on_chunk: ChunkCallback,
    ) -> AgentResponse:
        response = self.send_message(context, agent, user_prompt)
        if response.content:
            on_chunk(response.content)
        return response

    def health(self, agent: Agent) -> str:
        return "ok" if agent.enabled else "disabled"

    def plan(
        self,
        context: list[Message],
        agents: list[Agent],
        user_prompt: str,
        primary_agent: Agent,
        workdir: Path,
    ) -> PlanResult:
        return PlanResult(error=f"{primary_agent.name} 当前不支持任务规划。")


class CommandAgentAdapter(AgentAdapter):
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def send_message(self, context: list[Message], agent: Agent, user_prompt: str) -> AgentResponse:
        chunks: list[str] = []
        return self.stream_message(
            context,
            agent,
            user_prompt,
            self.settings.workspace,
            chunks.append,
        )

    def stream_message(
        self,
        context: list[Message],
        agent: Agent,
        user_prompt: str,
        workdir: Path,
        on_chunk: ChunkCallback,
    ) -> AgentResponse:
        prompt = self._prompt(context, agent, user_prompt, workdir)
        command = self._command(agent, workdir)
        if not command:
            content = f"{agent.name} 暂未配置可执行命令，无法真实执行任务。"
            return AgentResponse(content, error=content)
        executable = command[0]
        if not Path(executable).is_file() and not shutil.which(executable):
            content = (
                f"未找到 {executable}。请安装并登录对应 CLI，或通过 CLAUDE_CLI_PATH / "
                "CODEX_CLI_PATH 指定可执行文件。"
            )
            on_chunk(content)
            return AgentResponse(content, error=content)
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                command,
                cwd=workdir,
                text=True,
                stdin=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
        except OSError as exc:
            content = f"{agent.name} 无法启动：{exc}"
            on_chunk(content)
            return AgentResponse(content, error=content)

        assert process.stdin is not None
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        chunks: list[str] = []
        raw_lines: list[str] = []
        timed_out = False
        deadline = time.monotonic() + self.settings.agent_timeout_seconds
        assert process.stdout is not None
        import sys
        if sys.platform == "win32":
            import queue
            import threading

            q = queue.Queue()

            def reader(stream, q):
                for line in iter(stream.readline, ""):
                    q.put(line)
                stream.close()

            t = threading.Thread(target=reader, args=(process.stdout, q), daemon=True)
            t.start()

            while True:
                if time.monotonic() > deadline:
                    process.kill()
                    content = (
                        f"\n\n{agent.name} 执行超过 {self.settings.agent_timeout_seconds} 秒，已停止等待。"
                        "可以缩小任务范围或调大 AGENTHUB_AGENT_TIMEOUT_SECONDS。"
                    )
                    chunks.append(content)
                    on_chunk(content)
                    process.wait()
                    timed_out = True
                    break

                try:
                    line = q.get(timeout=0.2)
                    raw_lines.append(line)
                    chunk = self._stream_chunk(agent, line)
                    if chunk:
                        chunks.append(chunk)
                        on_chunk(chunk)
                except queue.Empty:
                    if process.poll() is not None:
                        while not q.empty():
                            line = q.get()
                            raw_lines.append(line)
                            chunk = self._stream_chunk(agent, line)
                            if chunk:
                                chunks.append(chunk)
                                on_chunk(chunk)
                        break
        else:
            while True:
                if time.monotonic() > deadline:
                    process.kill()
                    content = (
                        f"\n\n{agent.name} 执行超过 {self.settings.agent_timeout_seconds} 秒，已停止等待。"
                        "可以缩小任务范围或调大 AGENTHUB_AGENT_TIMEOUT_SECONDS。"
                    )
                    chunks.append(content)
                    on_chunk(content)
                    process.wait()
                    timed_out = True
                    break
                ready, _, _ = select.select([process.stdout], [], [], 0.2)
                if ready:
                    line = process.stdout.readline()
                    if line:
                        raw_lines.append(line)
                        chunk = self._stream_chunk(agent, line)
                        if chunk:
                            chunks.append(chunk)
                            on_chunk(chunk)
                        continue
                if process.poll() is not None:
                    remainder = process.stdout.read()
                    if remainder:
                        raw_lines.append(remainder)
                        chunk = self._stream_chunk(agent, remainder)
                        if chunk:
                            chunks.append(chunk)
                            on_chunk(chunk)
                    break

        output = "".join(chunks).strip()
        raw_output = "".join(raw_lines).strip()
        if timed_out:
            return AgentResponse(output, error=f"{agent.name} 执行超时")
        reported_error = _cli_reported_error(agent, raw_output)
        if reported_error:
            content = f"{agent.name} 执行失败：\n{reported_error}"
            on_chunk(f"\n\n{content}" if output else content)
            return AgentResponse(content, error=content)
        if process.returncode not in {0, None}:
            detail = _cli_error_detail(agent, raw_output, output)
            content = f"{agent.name} 执行失败，退出码 {process.returncode}：\n{detail}"
            on_chunk(f"\n\n{content}" if output else content)
            return AgentResponse(content, error=content)
        content = output or raw_output or f"{agent.name} 已完成任务，但 CLI 没有输出文本。"
        if not output:
            on_chunk(content)
        return AgentResponse(content=content, artifacts=_artifacts_from_markdown(content, agent.name))

    def plan(
        self,
        context: list[Message],
        agents: list[Agent],
        user_prompt: str,
        primary_agent: Agent,
        workdir: Path,
    ) -> PlanResult:
        prompt = _planning_prompt(context, agents, user_prompt, primary_agent, workdir)
        response = self.stream_message(
            context,
            primary_agent,
            prompt,
            workdir,
            lambda chunk: None,
        )
        if response.error:
            return PlanResult(error=response.error)
        return _parse_plan(response.content, primary_agent.name)

    def health(self, agent: Agent) -> str:
        command = self._command(agent, self.settings.workspace)
        if not command:
            return "missing-command"
        executable = command[0]
        return "ok" if Path(executable).is_file() or shutil.which(executable) else "missing-cli"

    def _command(self, agent: Agent, workdir: Path) -> list[str]:
        if agent.provider == "claude-code":
            executable = self._resolve_executable(
                self.settings.claude_cli_path, "claude", ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]
            )
            command = [
                executable,
                "--print",
                "--permission-mode",
                "acceptEdits",
                "--add-dir",
                str(workdir),
                "--append-system-prompt",
                agent.system_prompt,
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--verbose",
            ]
            if agent.model:
                command.extend(["--model", agent.model])
            return command
        if agent.provider == "codex":
            executable = self._resolve_executable(
                self.settings.codex_cli_path,
                "codex",
                [
                    "/Applications/Codex.app/Contents/Resources/codex",
                    "/usr/local/bin/codex",
                    "/opt/homebrew/bin/codex",
                ],
            )
            command = [
                executable,
                "exec",
                "--cd",
                str(workdir),
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--json",
            ]
            if agent.model:
                command.extend(["--model", agent.model])
            command.append("-")
            return command
        return []

    def _resolve_executable(self, configured: str, command: str, candidates: list[str]) -> str:
        if configured:
            return str(Path(configured).expanduser())
        discovered = shutil.which(command)
        if discovered:
            return discovered
        return next((candidate for candidate in candidates if Path(candidate).is_file()), command)

    def _prompt(
        self, context: list[Message], agent: Agent, user_prompt: str, workdir: Path
    ) -> str:
        transcript = _history_transcript(context)
        pinned = "\n".join(
            f"- {message.sender_name}: {_context_content(message.content)}"
            for message in context
            if message.pinned
        )
        return (
            f"{agent.system_prompt}\n\n"
            "你正在 AgentHub 聊天会话中作为一个真实执行 Agent 工作。"
            "开始执行前必须阅读下面的完整会话历史、置顶消息和 System Prompt。"
            f"当前会话唯一允许的项目工作目录是：{workdir}。"
            "所有新建、修改和生成的代码、文本、规范、日志和协作文档都必须保存在该目录中，"
            "不要在父仓库中创建或修改 AGENTS.md、docs 或其他文件。"
            "请直接完成用户任务；如修改了文件，请总结修改点、验证命令和结果。"
            "如果任务需要更多信息，请明确列出缺口。\n\n"
            f"置顶消息：\n{pinned or '暂无'}\n\n"
            f"完整会话历史：\n{transcript or '暂无'}\n\n"
            f"用户最新任务：\n{user_prompt}"
        )

    def _stream_chunk(self, agent: Agent, line: str) -> str:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if agent.provider in {"claude-code", "codex"}:
                return ""
            return line
        if agent.provider == "claude-code":
            if event.get("type") == "stream_event":
                delta = event.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta":
                    return str(delta.get("text", ""))
            if event.get("type") == "result" and not event.get("is_error"):
                return ""
            return ""
        if agent.provider == "codex":
            item = event.get("item", {})
            event_type = event.get("type", "")
            if item.get("type") == "agent_message" and event_type == "item.completed":
                return str(item.get("text", ""))
            if event_type == "error":
                return f"\n\n{event.get('message', 'Codex 执行出错')}"
            return ""
        return line


class DeepSeekAgentAdapter(AgentAdapter):
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def send_message(self, context: list[Message], agent: Agent, user_prompt: str) -> AgentResponse:
        chunks: list[str] = []
        return self.stream_message(
            context,
            agent,
            user_prompt,
            self.settings.workspace,
            chunks.append,
        )

    def stream_message(
        self,
        context: list[Message],
        agent: Agent,
        user_prompt: str,
        workdir: Path,
        on_chunk: ChunkCallback,
    ) -> AgentResponse:
        if not self.settings.deepseek_api_key:
            content = (
                "DeepSeek API Key 未配置。请在项目根目录 `.env` 中填写 DEEPSEEK_API_KEY，"
                "然后重启后端服务。"
            )
            on_chunk(content)
            return AgentResponse(content, error=content)
        messages = self._messages(context, agent, user_prompt, workdir)
        content, error = self._stream_complete(
            messages,
            agent.model or self.settings.deepseek_model,
            on_chunk,
        )
        if error:
            on_chunk(error)
            return AgentResponse(error, error=error)
        return AgentResponse(content=content, artifacts=_artifacts_from_markdown(content, agent.name))

    def plan(
        self,
        context: list[Message],
        agents: list[Agent],
        user_prompt: str,
        primary_agent: Agent,
        workdir: Path | None = None,
    ) -> PlanResult:
        if not self.settings.deepseek_api_key:
            return PlanResult(error="未配置 DEEPSEEK_API_KEY，请填写项目根目录 `.env` 并重启后端。")
        if not agents:
            return PlanResult(error="当前会话没有可调度的 Agent。")
        roster = "\n".join(
            f"- id={agent.id}; name={agent.name}; provider={agent.provider}; "
            f"execution={'filesystem' if agent.kind == 'cli' else 'text-only'}; "
            f"capabilities={agent.description or ', '.join(agent.capability_tags)}"
            for agent in agents
        )
        transcript = _history_transcript(context)
        messages = [
            {
                "role": "system",
                "content": (
                    f"{primary_agent.system_prompt}\n"
                    f"你是当前会话的主 Agent：{primary_agent.name}。理解用户需求，把任务拆成少量、"
                    "可执行、按顺序完成的子任务，并只选择提供的 Agent。"
                    "当用户使用 @all、所有人或明确要求全员协作时，tasks 必须覆盖除主 Agent 外的"
                    "每一个可用 Agent，且同一 Agent 不要重复出现；请按各自职责分配不同任务。"
                    "execution=filesystem 的 Agent 可以修改项目文件；execution=text-only 的 Agent "
                    "只能分析和输出文本，不能执行命令或直接创建文件。"
                    f"所有产物必须放在 {workdir} 中。"
                    "只返回 JSON，不要 Markdown。格式："
                    '{"summary":"调度说明","tasks":[{"agent_id":"id","instruction":"具体任务"}]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"可用 Agent：\n{roster}\n\n最近上下文：\n{transcript or '暂无'}"
                    f"\n\n用户最新需求：\n{user_prompt}"
                ),
            },
        ]
        content, error = self._complete(messages, self.settings.deepseek_model, temperature=0.2)
        if error:
            return PlanResult(error=error)
        try:
            parsed = json.loads(_strip_json_fence(content))
        except json.JSONDecodeError:
            return PlanResult(error="DeepSeek 返回的任务计划不是有效 JSON，已使用本地规则。")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tasks"), list):
            return PlanResult(error="DeepSeek 返回的任务计划缺少 tasks 数组，已使用本地规则。")
        return PlanResult(plan=parsed)

    def _complete(
        self, messages: list[dict[str, str]], model: str, temperature: float = 0.4
    ) -> tuple[str, str | None]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        request = urllib.request.Request(
            f"{self.settings.deepseek_base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with urllib.request.urlopen(
                request,
                timeout=self.settings.agent_timeout_seconds,
                context=context,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return "", f"DeepSeek 调用失败，HTTP {exc.code}：\n{detail}"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return "", "DeepSeek HTTPS 证书校验失败，请检查系统证书或代理配置。"
            return "", f"DeepSeek 网络调用失败：{exc.reason}"
        except TimeoutError:
            return "", f"DeepSeek 调用超过 {self.settings.agent_timeout_seconds} 秒，已停止等待。"
        except json.JSONDecodeError:
            return "", "DeepSeek 返回了无法解析的响应，请稍后重试。"

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            content = "DeepSeek 返回为空，请检查模型配置或稍后重试。"
        return content, None

    def _stream_complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        on_chunk: ChunkCallback,
    ) -> tuple[str, str | None]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "stream": True,
        }
        request = urllib.request.Request(
            f"{self.settings.deepseek_base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        chunks: list[str] = []
        try:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with urllib.request.urlopen(
                request,
                timeout=self.settings.agent_timeout_seconds,
                context=context,
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    chunk = (
                        event.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if chunk:
                        chunks.append(chunk)
                        on_chunk(chunk)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return "", f"DeepSeek 调用失败，HTTP {exc.code}：\n{detail}"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return "", "DeepSeek HTTPS 证书校验失败，请检查系统证书或代理配置。"
            return "", f"DeepSeek 网络调用失败：{exc.reason}"
        except TimeoutError:
            return "", f"DeepSeek 调用超过 {self.settings.agent_timeout_seconds} 秒，已停止等待。"
        except json.JSONDecodeError:
            return "", "DeepSeek 流式响应无法解析，请稍后重试。"
        content = "".join(chunks)
        return (content, None) if content else ("", "DeepSeek 返回为空，请稍后重试。")

    def health(self, agent: Agent) -> str:
        return "ok" if self.settings.deepseek_api_key else "missing-api-key"

    def _messages(
        self,
        context: list[Message],
        agent: Agent,
        user_prompt: str,
        workdir: Path,
    ) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": (
                    f"{agent.system_prompt}\n"
                    "你是 AgentHub 中通过 API 提供纯文本能力的真实 Agent。"
                    "你没有 Bash、终端或文件系统工具，不得输出 <bash> 等伪工具调用，"
                    "也不得声称已直接创建、修改或运行文件。"
                    f"当前会话的唯一产物归档目录是 {workdir}；你的文本回复会由系统归档到该目录。"
                    "开始工作前阅读完整会话历史、置顶消息和本 System Prompt，"
                    "然后输出可执行的分析、研究结论或文本内容。"
                ),
            }
        ]
        for message in context:
            role = "assistant" if message.role in {"agent", "orchestrator"} else "user"
            messages.append(
                {
                    "role": role,
                    "content": f"{message.sender_name}: {_context_content(message.content)}",
                }
            )
        messages.append({"role": "user", "content": user_prompt})
        return messages


class DisabledAgentAdapter(AgentAdapter):
    def send_message(self, context: list[Message], agent: Agent, user_prompt: str) -> AgentResponse:
        return AgentResponse(f"{agent.name} 当前未接入，本轮不会执行任务。")

    def health(self, agent: Agent) -> str:
        return "disabled"


class MockAgentAdapter(AgentAdapter):
    def send_message(self, context: list[Message], agent: Agent, user_prompt: str) -> AgentResponse:
        return AgentResponse(f"{agent.name} 测试适配器收到：{user_prompt}")


class AdapterRegistry:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.command = CommandAgentAdapter(self.settings)
        self.deepseek = DeepSeekAgentAdapter(self.settings)
        self.disabled = DisabledAgentAdapter()
        self.mock = MockAgentAdapter()

    def for_agent(self, agent: Agent) -> AgentAdapter:
        if not agent.enabled:
            return self.disabled
        if agent.provider in {"claude-code", "codex"} and agent.kind == "cli":
            return self.command
        if agent.provider == "deepseek" and agent.kind == "api":
            return self.deepseek
        if agent.kind == "mock":
            return self.mock
        return self.disabled


def _cli_error_detail(agent: Agent, raw_output: str, visible_output: str) -> str:
    candidates: list[str] = []
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line.strip() and not line.lstrip().startswith("{"):
                candidates.append(line.strip())
            continue
        if agent.provider == "claude-code":
            if event.get("type") == "result":
                result = str(event.get("result", "")).strip()
                if result:
                    candidates.append(result)
            message = event.get("message", {})
            for block in message.get("content", []) if isinstance(message, dict) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", "")).strip()
                    if text:
                        candidates.append(text)
        elif agent.provider == "codex":
            if event.get("type") == "error":
                candidates.append(str(event.get("message", "Codex 执行出错")).strip())
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = str(item.get("text", "")).strip()
                if text:
                    candidates.append(text)
    detail = next((item for item in reversed(candidates) if item), "")
    if not detail:
        detail = visible_output.strip() or "没有返回可读的错误详情。"
    return _limit_text(detail, 2000)


def _cli_reported_error(agent: Agent, raw_output: str) -> str | None:
    for line in reversed(raw_output.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            agent.provider == "claude-code"
            and event.get("type") == "result"
            and event.get("is_error")
        ):
            return _limit_text(
                str(event.get("result", "")).strip() or "Claude Code 返回执行错误。",
                2000,
            )
        if agent.provider == "codex" and event.get("type") == "error":
            return _limit_text(
                str(event.get("message", "")).strip() or "Codex 返回执行错误。",
                2000,
            )
    return None


def _history_transcript(context: list[Message]) -> str:
    return "\n".join(
        f"{message.sender_name}: {_context_content(message.content)}" for message in context
    )


def _context_content(content: str) -> str:
    if not content:
        return ""
    transport_markers = (
        '"type":"stream_event"',
        '"type": "stream_event"',
        '"subtype":"init"',
        '"subtype": "init"',
        '"thinking_delta"',
    )
    if any(marker in content for marker in transport_markers):
        matches = re.findall(
            r"(?:API Error|Argument list too long|执行超过|执行失败|无法启动)[^{}\n]{0,500}",
            content,
        )
        summary = "；".join(dict.fromkeys(match.strip() for match in matches if match.strip()))
        return f"[底层 CLI 传输日志已压缩]{' ' + summary if summary else ''}"
    return _limit_text(content, 12000)


def _limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n\n[内容过长，已压缩中间部分]\n\n{text[-tail:]}"


def _artifacts_from_markdown(content: str, source_name: str) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for index, match in enumerate(re.finditer(r"```([a-zA-Z0-9_+.-]*)\n(.*?)```", content, re.DOTALL), start=1):
        language = match.group(1) or "text"
        code = match.group(2).strip()
        artifact_type = "diff" if language.lower() in {"diff", "patch"} or code.startswith("--- ") else "code"
        artifacts.append(
            Artifact(
                id=f"art-{uuid.uuid4().hex[:8]}",
                type=artifact_type,
                title=f"{source_name} 代码片段 {index}",
                language=language,
                content=code,
            )
        )
    return artifacts


def _strip_json_fence(content: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else content.strip()


def _planning_prompt(
    context: list[Message],
    agents: list[Agent],
    user_prompt: str,
    primary_agent: Agent,
    workdir: Path,
) -> str:
    roster = "\n".join(
        f"- id={agent.id}; name={agent.name}; provider={agent.provider}; "
        f"execution={'filesystem' if agent.kind == 'cli' else 'text-only'}; "
        f"capabilities={agent.description or ', '.join(agent.capability_tags)}"
        for agent in agents
    )
    transcript = _history_transcript(context)
    pinned = "\n".join(
        f"- {message.sender_name}: {_context_content(message.content)}"
        for message in context
        if message.pinned
    )
    return (
        f"你是当前会话的主 Agent：{primary_agent.name}。开始规划前阅读完整历史、置顶消息和你的 "
        "System Prompt。把用户需求拆成少量、按顺序执行的任务，并只选择下列 Agent。"
        "主 Agent 负责统筹，可在确有必要时给自己分配执行任务。"
        "当用户使用 @all、所有人或明确要求全员协作时，tasks 必须覆盖除主 Agent 外的每一个"
        "可用 Agent，且同一 Agent 不要重复出现；请按各自职责分配不同任务。"
        "execution=filesystem 的 Agent 可以创建和修改文件；execution=text-only 的 Agent "
        "只能做分析、研究、规划和文本撰写，禁止给 text-only Agent 分配需要执行命令或写文件的任务。"
        f"所有代码、文本、规范和协作文档必须放在唯一项目目录 {workdir} 中，不得修改父仓库。"
        "只返回 JSON，不要 Markdown，格式："
        '{"summary":"调度说明","tasks":[{"agent_id":"id","instruction":"具体任务"}]}'
        f"\n\n可用 Agent：\n{roster}\n\n置顶消息：\n{pinned or '暂无'}"
        f"\n\n完整会话历史：\n{transcript or '暂无'}"
        f"\n\n用户最新需求：\n{user_prompt}"
    )


def _parse_plan(content: str, planner_name: str) -> PlanResult:
    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        return PlanResult(error=f"{planner_name} 返回的任务计划不是有效 JSON。")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tasks"), list):
        return PlanResult(error=f"{planner_name} 返回的任务计划缺少 tasks 数组。")
    return PlanResult(plan=parsed)
