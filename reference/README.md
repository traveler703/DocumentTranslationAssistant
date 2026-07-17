# AgentHub：多Agent协作平台

## 项目背景

平台采用即时（Instant Messaging，IM）聊天作为核心交互范式。用户像使用飞书/微信/Telegram等即时通讯软件一样，通过新建对话、发送消息的方式与不同 Al Agent 进行交互。每个 Agent 都是一个"聊天对象”，用户可以:

- 新建对话：创建一个新的聊天会话，选择或指定要对话的 Agent （如 Claude Code、Codex、OpenCode等）

- 多会话并行：同时开启多个会话窗口，分别与不同 Agent 交流不同任务（类似 IM 软件的多个聊天窗口）

- 群聊协作：在一个聊天会话中有多个 Agent（群聊），由主 Agent（Orchestrator）自动协调分工，多个 Agent 像群聊成员一样依次回复各自的产出。在会话中可以@某个 Agent或@所有人，别的 Agent 可以看到会话中的所有消息，即使自己没有被@到

- 上下文连续：每个聊天会话保持完整的聊天历史，Agent 能基于历史消息理解上下文，支持多轮迭代修改

- 产物内联：Agent 回复的内容不仅是文字，还可以内联展示代码 Diff、网页预览卡片、文件附件等多媒体产物，用户可直接在聊天流中预览和操作

平台同时接入市面主流 Agent 平台（Claude Code、Codex、OpenCode 等），通过统一的适配器层屏蔽 API 差异，并支持用户自建 Agent。所有 Agent 的产出（如代码、网页、文档、演示文稿等）支持实时预览、代码二次编辑和一键部署发布。

## 核心功能详细说明

### 1. IM 聊天式交互（核心功能）

| 功能点     | 说明                                                         |
| ---------- | ------------------------------------------------------------ |
| 会话列表   | 界面左侧为聊天会话列表，支持新建聊天会话/置顶聊天会话/归档聊天会话/搜索聊天会话等功能，按最近活跃排序（置顶的聊天会话一定排在其他会话的上面，对于同一类会话而言，最新消息发布时间最晚的聊天会话排在最上面） |
| 单聊模式   | 用户 1v1 与某个 Agent 对话，给特定的 Agent 发布命令          |
| 群聊模式   | 一个聊天会话中包含多个 Agent，通过 @ 指定或由 Orchestrator 自动分派，被@ 的 Agent 依次回复 |
| 消息类型   | 文本、代码块、图片、文件附件、网页预览卡片、代码修改差异 Diff 视图卡片 |
| 消息操作   | 回复、引用、重新生成、复制代码、一键应用 Diff（即 “接受/Accept” 或 “拒绝/Decline” 某 Agent提出的代码修改内容）、展开预览 |
| 上下文管理 | 聊天历史自动作为上下文传递给 Agent，支持手动置顶关键消息作为该聊天会话的长期上下文 |

### 2. 主 Agent 协调器(Orchestrator)

- 在群聊模式下，自动理解用户意图，将复杂任务拆解并分派给合适的子 Agent
- 子 Agent 完成后，Orchestrator 聚合产出并在聊天流中汇报结果
- 支持并行调度、失败降级、代码冲突处理

### 3. 多 Agent 接入

- 统一适配器层，至少接入 2 个主流 Agent 平台(Claude Code/Codex/OpenCode)
- 支持用户自建 Agent（对话式创建，可以给每个 Agent 设定 System Prompt + 工具集）
- 每个 Agent 在联系人列表中显示为独立的“联系人"，有头像、名称、能力标签

### 4. 产物预览与编辑

- Agent 回复中内联产物预览卡片（网页iframe、文档渲染、PPT浏览）
- 点击卡片展开全屏预览/代码编辑器
- 支持代码 Diff 视图、版本历史查看、对话式局部修改（选中代码 → 在聊天中描述修改）

## 考察要点

| 要点         | 权重 | 描述                                              |
| ------------ | ---- | ------------------------------------------------- |
| AI 协作能力  | 30%  | 总结整理出和AI协作的Spec、Skills、Rules等协作规范 |
| 功能完整度   | 25%  | IM 核心体验是否流畅、多 Agent 调度是否跑通        |
| 生成效果质量 | 20%  | 聊天 UI体验、产物预览效果                         |
| 代码理解度   | 15%  | 答辩时能否解释架构选型和核心逻辑                  |
| 创新与产品感 | 10%  | 超预期功能点或体验优化、详细的产品设计方案        |

**交付物**：产品设计文档 + 技术文档 + 可运行 Demo + Al 协作开发记录 + 3 分钟 Demo 视频

## 当前实现

本仓库已按上述目标实现第一版本地 Demo：

- `backend/`：FastAPI + SQLite 后端，包含会话、消息、Agent、Artifact、Orchestrator 和统一 Adapter。
- `frontend/`：React + Vite 前端，提供 IM 会话列表、聊天流、Agent 联系人、产物卡片和 Diff 操作。
- `docs/`：产品设计文档、技术文档、AI 协作开发记录和 3 分钟 Demo 视频脚本。

默认接入 Claude Code 与 Codex 两个真实 CLI Agent，暂不接入 OpenCode。用户自建 Agent 使用 DeepSeek API，配置写在项目根目录 `.env`。

## 本地启动

后端：

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn agenthub.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`。

真实 Agent 要求：

- Claude Code：本机可执行 `claude`，并已完成登录或 API Key 配置。
- Codex：本机可执行 `codex`，并已完成登录。
- DeepSeek：`.env` 中填写 `DEEPSEEK_API_KEY`，用于用户自建 Agent。

## 验证

```bash
cd backend
PYTHONPATH=. pytest

cd frontend
npm run build
```
