# AgentHub AI 协作规则

## 每轮工作流程

1. 先把用户的新指令整理到 `docs/ai-work-spec.md` 的“当前迭代”中。
2. 修改前阅读 `README.md`、`docs/ai-work-spec.md`，以及本次涉及模块的产品和技术文档。
3. 明确本轮目标、可用工具、允许修改范围和验收标准，不处理范围外重构。
4. 实现后至少运行后端测试和前端构建；涉及界面时还要在浏览器中检查关键流程。
5. 把实现结果、验证结果和未解决风险追加到 `docs/ai-collaboration-log.md`。
6. 测试后保留 `.venv/`、`node_modules/` 等运行环境；只删除测试会话的 Agent 项目产物，保留测试聊天记录。

## 产品边界

- AgentHub 的核心只有两部分：主流 IM 式聊天体验，以及通过 Agent 完成真实任务。
- 默认主 Agent 为联系人中的 System Agent；切换后由新的主 Agent 使用自身 Adapter 规划，不得固定依赖 DeepSeek。
- 系统预设 Agent 包含 System Agent、Claude Code 和 Codex；不展示或启用 OpenCode。
- 用户自建 Agent 使用 DeepSeek API，必须具有名称、System Prompt 和功能说明。
- System Agent、Claude Code、Codex 是不可删除的内置联系人；其他联系人可从联系人列表移除。
- 联系人保存 Agent 默认职责与 System Prompt；会话成员保存可独立覆盖的名称、职责和 System Prompt。
- 右侧成员面板只展示当前会话中的 Agent，不展示全局联系人。

## 工程规则

- 复用 FastAPI、SQLite、React、Vite 和现有 Adapter 架构，不引入无必要的新框架。
- 所有 Agent 平台差异留在 Adapter 层；前端不得拼接 CLI 命令。
- 会话、联系人、成员关系和消息必须持久化，刷新页面后仍然有效。
- 群聊必须且只能有一个主 Agent；新建群聊默认仅加入 System Agent。
- 单聊 Agent 独立完成任务；群聊由当前主 Agent 规划，其余 Agent 按计划顺序执行。
- 主 Agent 的计划消息必须按顺序 `@Agent`，并明确写出当前会话唯一允许的 `projects/{conversation_id}/` 工作目录。
- 每个 Agent 执行前必须读取完整会话历史、置顶消息和自己的会话级 System Prompt。
- 纯 API Agent 只能承担分析、研究、规划和文本任务；创建或修改项目文件的任务必须分配给具备文件工具的 CLI Agent。
- 后台 Agent 执行不得阻塞发送消息接口；错误应作为聊天消息返回，并给出可操作原因。
- 主 Agent 规划失败必须本地降级；执行 Agent 失败时应把未完成任务转交其他可用 Agent。
- Agent 回复默认流式写入同一条消息，并以 Markdown 安全渲染。
- Agent 只能在 `projects/{conversation_id}/` 中创建或修改项目文件，代码、文本、规范和协作文档均不得写入仓库父目录。
- CLI 提示词通过标准输入传递；聊天中不得保存 CLI 的初始化、思考增量和其他底层传输事件。
- 不读取、记录或提交 `.env` 中的密钥。
- 不回退用户已有未提交修改；只在当前需求涉及的文件内继续完善。

## 完成定义

- 用户能在“群组”中查看群聊，在“联系人”中选择联系人并发起单聊。
- 会话支持重命名、置顶、归档和删除；归档会话可查看但不能继续发言。
- 当前会话成员正确显示在右侧。
- 用户能创建自建 Agent，并选择仅加入当前会话或同时保存到联系人。
- 当前主 Agent 能通过自身 Adapter 生成任务计划；不可用时有本地降级和任务转交。
- Claude Code 和 Codex 能通过可配置的本机 CLI 路径启动。
- 三栏独立滚动，流式消息与 Markdown 内容可正常展示。
- 每个会话具有独立项目目录。
- 后端测试、前端构建和浏览器关键流程通过。
