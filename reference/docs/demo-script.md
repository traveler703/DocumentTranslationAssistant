# 3 分钟 Demo 视频脚本

## 0:00 - 0:25 开场

打开 AgentHub，展示左侧会话列表、中间聊天流、右侧 Agent 联系人。说明这是一个用 IM 范式组织多 Agent 协作的平台。

## 0:25 - 0:55 新建会话

点击“群聊”，创建新的 Agent 群聊。展示 Claude Code、Codex 的头像、能力标签、CLI/API 类型和健康状态。

## 0:55 - 1:35 群聊协作

在输入框发送：

```text
@all 请拆解并实现一个带网页预览和 Diff 的功能
```

展示 Orchestrator 先汇总分派，然后 Claude Code 与 Codex 通过真实 CLI 依次回复。

## 1:35 - 2:10 产物预览

展开网页预览卡片，展示 iframe 内联预览。展开 Diff 卡片，点击 Accept，再展示状态变化。

## 2:10 - 2:35 上下文管理

选择一条关键消息点击置顶，说明它会成为长期上下文。点击回复或重新生成，展示多轮迭代入口。

## 2:35 - 3:00 自建 Agent 与技术亮点

在右侧创建一个自建 Agent。说明该 Agent 使用 `.env` 中的 DeepSeek API Key，并会自动打开一个单聊会话。最后切到技术文档，说明后端通过统一 Adapter Registry 支持 CLI 与 API Agent。
