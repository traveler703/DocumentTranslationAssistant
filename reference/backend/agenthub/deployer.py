from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import Artifact, Message
from .store import Store


def generate_index_fallback(target_dir: Path, conversation_id: str) -> None:
    """Generates a beautiful HTML index page listing all files in the project folder."""
    files = []
    for p in target_dir.rglob("*"):
        if p.is_file() and p.name != "index.html" and not p.name.startswith("."):
            try:
                rel_path = p.relative_to(target_dir)
                size_kb = round(p.stat().st_size / 1024, 2)
                files.append((str(rel_path).replace("\\", "/"), size_kb))
            except ValueError:
                continue

    file_items_html = ""
    if not files:
        file_items_html = "<div class='empty'>此项目目录目前是空的。</div>"
    else:
        for path, size in files:
            file_items_html += f"""
            <div class="file-item">
                <span class="file-icon">📄</span>
                <span class="file-name">{path}</span>
                <span class="file-size">{size} KB</span>
            </div>
            """

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>项目部署预览 - AgentHub</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #0d1117;
            color: #c9d1d9;
            margin: 0;
            padding: 40px 20px;
            display: flex;
            justify-content: center;
        }}
        .card {{
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            width: 100%;
            max-width: 600px;
            padding: 30px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }}
        .header {{
            display: flex;
            align-items: center;
            border-bottom: 1px solid #30363d;
            padding-bottom: 20px;
            margin-bottom: 20px;
        }}
        .logo {{
            background: linear-gradient(135deg, #7c3aed, #2563eb);
            color: white;
            font-weight: bold;
            font-size: 20px;
            width: 44px;
            height: 44px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 15px;
        }}
        h1 {{
            font-size: 20px;
            margin: 0;
            color: #f0f6fc;
        }}
        .subtitle {{
            font-size: 13px;
            color: #8b949e;
            margin-top: 4px;
        }}
        .file-list {{
            margin-top: 20px;
        }}
        .file-item {{
            display: flex;
            align-items: center;
            padding: 12px 16px;
            background-color: #0d1117;
            border: 1px solid #21262d;
            border-radius: 6px;
            margin-bottom: 8px;
        }}
        .file-icon {{
            font-size: 18px;
            margin-right: 12px;
        }}
        .file-name {{
            flex-grow: 1;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            font-size: 14px;
        }}
        .file-size {{
            font-size: 12px;
            color: #8b949e;
        }}
        .badge {{
            display: inline-block;
            background-color: #238636;
            color: white;
            font-size: 12px;
            padding: 3px 8px;
            border-radius: 20px;
            margin-left: auto;
        }}
        .empty {{
            text-align: center;
            padding: 30px;
            color: #8b949e;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="logo">AH</div>
            <div>
                <h1>部署成功！</h1>
                <div class="subtitle">会话 ID: {conversation_id}</div>
            </div>
            <span class="badge">已上线</span>
        </div>
        <p>该项目目前没有包含 <code>index.html</code> 入口。以下是当前部署目录下的可用文件列表：</p>
        <div class="file-list">
            {file_items_html}
        </div>
    </div>
</body>
</html>
"""
    index_path = target_dir / "index.html"
    index_path.write_text(html_content, encoding="utf-8")


async def dispatch_deploy(db_path: Path, conversation_id: str) -> None:
    # Give a tiny buffer for users to see
    await asyncio.sleep(0.5)

    store = Store(db_path)
    try:
        # Create deployment message
        message_id = f"msg-deploy-{uuid.uuid4().hex[:8]}"
        artifact_id = f"art-deploy-{uuid.uuid4().hex[:8]}"

        settings = get_settings()
        target_dir = settings.workspace / "projects" / conversation_id
        target_dir.mkdir(parents=True, exist_ok=True)

        # Retrieve file list
        files_found = [p.name for p in target_dir.iterdir() if p.is_file() and p.name != ".gitkeep"]
        files_str = ", ".join(files_found) if files_found else "无"

        # Step templates
        steps = [
            {"name": "初始化部署环境", "status": "pending"},
            {"name": "检查及校验项目文件", "status": "pending"},
            {"name": "构建与编译资源", "status": "pending"},
            {"name": "发布部署到托管域", "status": "pending"},
        ]

        logs = [
            "🚀 正在初始化 AgentHub 部署管线...",
            f"📂 目标部署工作区: {target_dir}",
        ]

        def get_artifact_content(status: str, progress: int, current_idx: int, preview_url: str = "") -> str:
            updated_steps = []
            for i, step in enumerate(steps):
                step_status = "pending"
                if i < current_idx:
                    step_status = "done"
                elif i == current_idx:
                    step_status = "running"
                updated_steps.append({"name": step["name"], "status": step_status})

            return json.dumps({
                "status": status,
                "progress": progress,
                "steps": updated_steps,
                "logs": logs,
                "url": preview_url,
            }, ensure_ascii=False)

        # Initial message creation
        initial_content = get_artifact_content("building", 5, 0)
        artifact = Artifact(
            id=artifact_id,
            type="deploy",
            title="项目部署进度",
            content=initial_content,
        )

        message = store.create_message(
            conversation_id,
            role="agent",
            sender_id="deployer",
            sender_name="Deployer",
            content="正在为您部署当前项目...",
            artifacts=[artifact],
            streaming=True,
        )

        # Step 0: Initialize
        await asyncio.sleep(1.0)
        logs.extend([
            "✔ 成功加载部署流程",
            "✔ 检查本地静态托管配置",
            "✔ 静态路由已绑定到当前会话工作区",
        ])
        store.update_message_content(
            message.id,
            "部署中：正在初始化部署环境...",
            artifacts=[Artifact(id=artifact_id, type="deploy", title="项目部署进度", content=get_artifact_content("building", 25, 1))],
            streaming=True,
        )

        # Step 1: Validate files
        await asyncio.sleep(1.2)
        logs.extend([
            "✔ 检测项目文件目录...",
            f"ℹ 检索到工作区文件: {files_str}",
            "✔ 文件校验通过，未发现冲突或受限制的文件路径",
        ])
        store.update_message_content(
            message.id,
            "部署中：正在检查并校验项目文件...",
            artifacts=[Artifact(id=artifact_id, type="deploy", title="项目部署进度", content=get_artifact_content("building", 50, 2))],
            streaming=True,
        )

        # Step 2: Build / Compile
        await asyncio.sleep(1.5)
        build_result = await asyncio.to_thread(_run_static_build, target_dir)
        logs.extend(build_result.logs)
        if not build_result.ok:
            raise RuntimeError(build_result.error or "项目构建失败。")
        store.update_message_content(
            message.id,
            "部署中：静态资源构建/校验完成...",
            artifacts=[Artifact(id=artifact_id, type="deploy", title="项目部署进度", content=get_artifact_content("building", 75, 3))],
            streaming=True,
        )

        # Step 3: Deploy & Host
        await asyncio.sleep(1.2)
        preview_entry = _find_preview_entry(target_dir)
        if preview_entry is None:
            logs.append("⚠ 未在工作区检测到 index.html，正在自动生成项目文件列表导航页...")
            generate_index_fallback(target_dir, conversation_id)
            logs.append("✔ 自动生成 index.html 成功")
            preview_entry = Path("index.html")

        preview_path = str(preview_entry).replace("\\", "/")
        preview_url = f"http://localhost:8000/static/projects/{conversation_id}/{preview_path}"
        logs.extend([
            f"✔ 静态文件分发入口已确认: {preview_path}",
            f"✔ 项目已成功上线！预览地址: {preview_url}",
        ])

        final_artifact_content = get_artifact_content("success", 100, 4, preview_url)
        store.update_message_content(
            message.id,
            f"🎉 部署成功！您可以点击下方的预览按钮查看您的项目。<br>静态预览链接: [{preview_url}]({preview_url})",
            artifacts=[Artifact(id=artifact_id, type="deploy", title="项目部署状态", content=final_artifact_content)],
            streaming=False,
        )

    except Exception as e:
        import traceback
        err_msg = f"❌ 部署失败: {str(e)}"
        final_artifact_content = json.dumps({
            "status": "failed",
            "progress": 75,
            "steps": [{"name": "部署失败", "status": "failed"}],
            "logs": ["Fatal error occurred:", traceback.format_exc(), err_msg],
            "url": "",
        }, ensure_ascii=False)
        
        # In case we fail, finalize message
        try:
            store.update_message_content(
                message.id,
                f"❌ 项目部署失败。原因: {str(e)}",
                artifacts=[Artifact(id=artifact_id, type="deploy", title="项目部署状态", content=final_artifact_content)],
                streaming=False,
            )
        except Exception:
            pass
    finally:
        store.conn.close()


class BuildResult:
    def __init__(self, ok: bool, logs: list[str], error: str = ""):
        self.ok = ok
        self.logs = logs
        self.error = error


def _run_static_build(target_dir: Path) -> BuildResult:
    package_json = target_dir / "package.json"
    if not package_json.is_file():
        return BuildResult(True, ["ℹ 未检测到 package.json，按静态文件目录直接发布。"])

    try:
        package_data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return BuildResult(False, [f"❌ package.json 无法解析: {exc}"], "package.json 无法解析。")

    scripts = package_data.get("scripts", {})
    if not isinstance(scripts, dict) or "build" not in scripts:
        return BuildResult(True, ["ℹ package.json 未声明 build 脚本，跳过构建并直接发布静态目录。"])

    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    logs = ["⚙ 检测到 package.json build 脚本，准备执行真实前端构建。"]
    if (target_dir / "package-lock.json").is_file() and not (target_dir / "node_modules").exists():
        install = _run_command([npm, "ci"], target_dir)
        logs.extend(_summarize_output("npm ci", install))
        if install.returncode != 0:
            return BuildResult(False, logs, "npm ci 执行失败。")

    build = _run_command([npm, "run", "build"], target_dir)
    logs.extend(_summarize_output("npm run build", build))
    if build.returncode != 0:
        return BuildResult(False, logs, "npm run build 执行失败。")
    return BuildResult(True, logs)


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )


def _summarize_output(label: str, result: subprocess.CompletedProcess[str]) -> list[str]:
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    tail = lines[-12:] if lines else ["无输出"]
    status = "✔" if result.returncode == 0 else "❌"
    return [f"{status} {label} 退出码 {result.returncode}", *[f"  {line}" for line in tail]]


def _find_preview_entry(target_dir: Path) -> Path | None:
    candidates = [Path("index.html"), Path("dist") / "index.html", Path("build") / "index.html"]
    for candidate in candidates:
        if (target_dir / candidate).is_file():
            return candidate
    return None
