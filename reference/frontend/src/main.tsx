import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Archive,
  Bot,
  Check,
  Code2,
  Contact,
  Copy,
  FileText,
  Globe,
  MessageSquarePlus,
  Minus,
  Pencil,
  Pin,
  Plus,
  RefreshCcw,
  Search,
  Send,
  Trash2,
  Users,
  X
} from "lucide-react";
import { api } from "./api";
import type { Agent, Artifact, Conversation, ConversationMember, Message } from "./types";
import "./styles.css";

type SidebarView = "groups" | "contacts" | "archived";
type AgentScope = "conversation" | "contacts";

const emptyAgentForm = {
  name: "",
  systemPrompt: "",
  description: "",
  scope: "conversation" as AgentScope
};

function App() {
  const [contacts, setContacts] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [archivedConversations, setArchivedConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeMembers, setActiveMembers] = useState<ConversationMember[]>([]);
  const [query, setQuery] = useState("");
  const [sidebarView, setSidebarView] = useState<SidebarView>("groups");
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [expandedArtifact, setExpandedArtifact] = useState<Artifact | null>(null);
  const [respondingConversationIds, setRespondingConversationIds] = useState<string[]>([]);
  const [isAgentModalOpen, setAgentModalOpen] = useState(false);
  const [isAddMemberModalOpen, setAddMemberModalOpen] = useState(false);
  const [isRemoveContactModalOpen, setRemoveContactModalOpen] = useState(false);
  const [selectedMember, setSelectedMember] = useState<ConversationMember | null>(null);
  const [agentForm, setAgentForm] = useState(emptyAgentForm);
  const [isEditingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [rightPanelTab, setRightPanelTab] = useState<"members" | "history">("members");
  const [projectFiles, setProjectFiles] = useState<string[]>([]);
  const [versionHistory, setVersionHistory] = useState<any[]>([]);
  const [isParallel, setParallel] = useState(false);
  const [conflictArtifact, setConflictArtifact] = useState<{ artifact: Artifact; messageId: string } | null>(null);
  const allConversations = useMemo(
    () => [...conversations, ...archivedConversations.filter((item) => !conversations.some((open) => open.id === item.id))],
    [conversations, archivedConversations]
  );
  const activeConversation = allConversations.find((item) => item.id === activeId);
  const visibleGroups = conversations.filter(
    (conversation) =>
      conversation.mode === "group" &&
      `${conversation.title} ${conversation.last_message}`.toLowerCase().includes(query.toLowerCase())
  );
  const visibleArchived = archivedConversations.filter(
    (conversation) =>
      conversation.archived &&
      `${conversation.title} ${conversation.last_message}`.toLowerCase().includes(query.toLowerCase())
  );
  const visibleContacts = contacts.filter((agent) =>
    `${agent.name} ${agent.description} ${agent.capability_tags.join(" ")}`.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setActiveMembers([]);
      return;
    }
    void loadConversation(activeId);
  }, [activeId]);

  async function loadFilesAndVersions(id: string) {
    if (!id) return;
    try {
      const [filesList, versionsList] = await Promise.all([
        api.files(id),
        api.versions(id)
      ]);
      setProjectFiles(filesList);
      setVersionHistory(versionsList);
    } catch (e) {
      console.error("Failed to load files/versions", e);
    }
  }

  useEffect(() => {
    if (!activeId || activeConversation?.archived) return;
    const events = api.conversationEvents(activeId);
    events.onmessage = (event) => {
      const message = JSON.parse(event.data) as Message;
      setMessages((current) => mergeMessages(current, [message]));
      if ((message.role === "agent" || message.role === "orchestrator") && !message.streaming) {
        setRespondingConversationIds((current) => current.filter((id) => id !== activeId));
        void refreshData();
      }
    };
    return () => events.close();
  }, [activeId, activeConversation?.archived]);

  async function bootstrap() {
    const [contactList, openList, allList] = await Promise.all([
      api.contacts(),
      api.conversations(),
      api.conversations("", true)
    ]);
    setContacts(contactList);
    setConversations(openList);
    setArchivedConversations(allList.filter((item) => item.archived));
    const defaultId = openList.find((item) => item.mode === "group")?.id ?? openList[0]?.id ?? "";
    setActiveId(defaultId);
    if (defaultId) void loadFilesAndVersions(defaultId);
  }

  async function refreshData() {
    const [contactList, openList, allList] = await Promise.all([
      api.contacts(),
      api.conversations(),
      api.conversations("", true)
    ]);
    setContacts(contactList);
    setConversations(openList);
    setArchivedConversations(allList.filter((item) => item.archived));
    if (activeId) {
      void loadFilesAndVersions(activeId);
    }
  }

  async function loadConversation(id: string) {
    const detail = await api.conversation(id);
    setMessages(detail.messages);
    setActiveMembers(detail.members);
    setTitleDraft(detail.conversation.title);
    void loadFilesAndVersions(id);
  }

  async function createGroup() {
    const conversation = await api.createConversation({
      title: "新的 Agent 群聊",
      mode: "group",
      agent_ids: []
    });
    await refreshData();
    setSidebarView("groups");
    setActiveId(conversation.id);
  }

  async function openContact(agent: Agent) {
    const existing = conversations.find(
      (conversation) =>
        conversation.mode === "single" &&
        conversation.agent_ids.length === 1 &&
        conversation.agent_ids[0] === agent.id
    );
    if (existing) {
      setActiveId(existing.id);
      return;
    }
    const conversation = await api.createConversation({
      title: `与 ${agent.name} 单聊`,
      mode: "single",
      agent_ids: [agent.id]
    });
    await refreshData();
    setActiveId(conversation.id);
  }

  async function sendMessage() {
    if (!activeId || !draft.trim() || activeConversation?.archived) return;
    const conversationId = activeId;
    const content = draft.trim();
    const replyToId = replyTo?.id;
    const optimisticMessage: Message = {
      id: `optimistic-${crypto.randomUUID()}`,
      conversation_id: conversationId,
      role: "user",
      sender_id: "user",
      sender_name: "You",
      content,
      artifacts: [],
      reply_to: replyToId,
      pinned: false,
      streaming: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    setDraft("");
    setReplyTo(null);
    setMessages((current) => [...current, optimisticMessage]);
    setRespondingConversationIds((current) =>
      current.includes(conversationId) ? current : [...current, conversationId]
    );
    try {
      const response = await api.sendMessage(conversationId, content, replyToId, isParallel);
      setMessages((current) =>
        mergeMessages(
          current.filter((message) => message.id !== optimisticMessage.id),
          [response.user_message, ...response.agent_messages]
        )
      );
      await refreshData();
    } catch (error) {
      setMessages((current) => current.filter((message) => message.id !== optimisticMessage.id));
      setRespondingConversationIds((current) => current.filter((id) => id !== conversationId));
      setDraft(content);
      console.error(error);
    }
  }

  async function triggerDeploy() {
    if (!activeId || activeConversation?.archived) return;
    const conversationId = activeId;
    const content = "🚀 部署当前项目";
    const optimisticMessage: Message = {
      id: `optimistic-${crypto.randomUUID()}`,
      conversation_id: conversationId,
      role: "user",
      sender_id: "user",
      sender_name: "You",
      content,
      artifacts: [],
      reply_to: null,
      pinned: false,
      streaming: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    setDraft("");
    setReplyTo(null);
    setMessages((current) => [...current, optimisticMessage]);
    setRespondingConversationIds((current) =>
      current.includes(conversationId) ? current : [...current, conversationId]
    );
    try {
      const response = await api.sendMessage(conversationId, content, null);
      setMessages((current) =>
        mergeMessages(
          current.filter((message) => message.id !== optimisticMessage.id),
          [response.user_message, ...response.agent_messages]
        )
      );
      await refreshData();
    } catch (error) {
      setMessages((current) => current.filter((message) => message.id !== optimisticMessage.id));
      setRespondingConversationIds((current) => current.filter((id) => id !== conversationId));
      console.error(error);
    }
  }

  async function saveTitle() {
    if (!activeConversation || !titleDraft.trim()) return;
    await api.updateConversation(activeConversation.id, { title: titleDraft.trim() });
    setEditingTitle(false);
    await refreshData();
  }

  async function createAgent() {
    if (!activeConversation || !agentForm.name.trim() || !agentForm.systemPrompt.trim() || !agentForm.description.trim()) {
      return;
    }
    const agent = await api.createAgent({
      name: agentForm.name.trim(),
      avatar: agentForm.name.trim().slice(0, 2).toUpperCase(),
      kind: "api",
      provider: "deepseek",
      capability_tags: ["自建", "DeepSeek"],
      system_prompt: agentForm.systemPrompt.trim(),
      description: agentForm.description.trim(),
      tools: ["deepseek-v4-pro"],
      in_contacts: agentForm.scope === "contacts",
      model: "deepseek-v4-pro"
    });
    await api.updateConversation(activeConversation.id, {
      agent_ids: [...new Set([...activeConversation.agent_ids, agent.id])]
    });
    setAgentForm(emptyAgentForm);
    setAgentModalOpen(false);
    await refreshData();
    await loadConversation(activeConversation.id);
  }

  async function previewFile(filename: string) {
    if (!activeId) return;
    try {
      const file = await api.fileContent(activeId, filename);
      const ext = filename.split('.').pop() || '';
      setExpandedArtifact({
        id: `file-preview-${filename}`,
        type: "code",
        title: filename,
        content: file.content,
        language: ext,
        status: "accepted"
      });
    } catch (e) {
      alert(`无法打开文件：${String(e)}`);
    }
  }

  async function handleRevert(versionId: string) {
    if (!activeId || !window.confirm("确定要将整个工作区回滚到该版本吗？未提交的代码将会丢失。")) return;
    try {
      await api.revertVersion(activeId, versionId);
      alert("回滚成功！");
      void refreshData();
    } catch (e) {
      alert(`回滚失败：${String(e)}`);
    }
  }

  async function addMember(agent: Agent) {
    if (!activeConversation) return;
    await api.addConversationMember(activeConversation.id, agent.id);
    setAddMemberModalOpen(false);
    await refreshData();
    await loadConversation(activeConversation.id);
  }

  async function saveMember(member: ConversationMember) {
    if (!activeConversation || activeConversation.mode !== "group") return;
    await api.updateConversationMember(activeConversation.id, member.id, {
      name: member.name.trim(),
      description: member.description.trim(),
      system_prompt: member.system_prompt.trim(),
      is_primary: member.is_primary
    });
    setSelectedMember(null);
    await refreshData();
    await loadConversation(activeConversation.id);
  }

  async function removeContacts(agentIds: string[]) {
    await Promise.all(agentIds.map((agentId) => api.removeContact(agentId)));
    setRemoveContactModalOpen(false);
    await refreshData();
  }

  async function toggleConversationPin() {
    if (!activeConversation) return;
    await api.pinConversation(activeConversation.id, !activeConversation.pinned);
    await refreshData();
  }

  async function archiveConversation() {
    if (!activeConversation) return;
    await api.archiveConversation(activeConversation.id, true);
    await refreshData();
    setSidebarView("archived");
  }

  async function deleteConversation() {
    if (!activeConversation || !window.confirm(`永久删除“${activeConversation.title}”及其聊天记录？`)) return;
    await api.deleteConversation(activeConversation.id);
    setActiveId("");
    await refreshData();
  }

  async function updateArtifact(message: Message, artifact: Artifact, status: Artifact["status"]) {
    const updated = await api.updateArtifact(message.conversation_id, message.id, artifact, status);
    setMessages((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  }

  async function toggleMessagePin(message: Message) {
    const updated = await api.pinMessage(message.id, !message.pinned);
    setMessages((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">AH</div>
          <div><h1>AgentHub</h1><p>多 Agent 协作 IM</p></div>
        </div>
        <div className="searchBox">
          <Search size={17} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索" />
        </div>
        <div className="primaryActions">
          <button className={sidebarView === "groups" ? "active" : ""} onClick={() => setSidebarView("groups")}>
            <Users size={17} />群组
          </button>
          <button className={sidebarView === "contacts" ? "active" : ""} onClick={() => setSidebarView("contacts")}>
            <Contact size={17} />联系人
          </button>
        </div>
        <div className="sidebarUtilityRow">
          <button className={sidebarView === "archived" ? "archiveNav active" : "archiveNav"} onClick={() => setSidebarView("archived")}>
            <Archive size={16} />归档会话
          </button>
          {sidebarView === "contacts" && (
            <button className="removeContactIcon" title="删除联系人" onClick={() => setRemoveContactModalOpen(true)}>
              <Minus size={18} />
            </button>
          )}
        </div>
        <nav className="conversationList">
          {sidebarView === "groups" && (
            <>
              <button className="newGroup" onClick={createGroup}><MessageSquarePlus size={16} />新建群聊</button>
              {visibleGroups.map((conversation) => (
                <ConversationButton key={conversation.id} conversation={conversation} activeId={activeId} onClick={setActiveId} />
              ))}
            </>
          )}
          {sidebarView === "contacts" && visibleContacts.map((agent) => (
            <button key={agent.id} className="contactRow" onClick={() => openContact(agent)}>
              <span className="miniAvatar">{agent.avatar}</span>
              <span><strong>{agent.name}</strong><small>{agent.description || agent.capability_tags.join(" · ")}</small></span>
              <i className={`healthDot ${agent.health === "ok" ? "ok" : ""}`} title={agent.health} />
            </button>
          ))}
          {sidebarView === "archived" && visibleArchived.map((conversation) => (
            <ConversationButton key={conversation.id} conversation={conversation} activeId={activeId} onClick={setActiveId} />
          ))}
        </nav>
      </aside>

      <section className="chat">
        <header className="chatHeader">
          <div className="titleBlock">
            {isEditingTitle ? (
              <div className="titleEditor">
                <input value={titleDraft} onChange={(event) => setTitleDraft(event.target.value)} autoFocus />
                <button onClick={saveTitle}><Check size={16} /></button>
                <button onClick={() => setEditingTitle(false)}><X size={16} /></button>
              </div>
            ) : (
              <button className="editableTitle" disabled={!activeConversation} onClick={() => setEditingTitle(true)}>
                <h2>{activeConversation?.title ?? "选择群组或联系人开始聊天"}</h2>
                {activeConversation && <Pencil size={14} />}
              </button>
            )}
            {activeConversation && (
              <p>
                {activeConversation.archived ? "已归档 · 只读" : activeConversation.mode === "group" ? "群聊协作" : "单 Agent 对话"}
                {" · "}{activeMembers.length} Agent
              </p>
            )}
          </div>
          {activeConversation && (
            <div className="headerActions">
              <button title="置顶会话" className={activeConversation.pinned ? "selected" : ""} onClick={toggleConversationPin}><Pin size={17} /></button>
              {!activeConversation.archived && <button title="归档会话" onClick={archiveConversation}><Archive size={17} /></button>}
              <button title="永久删除会话" className="danger" onClick={deleteConversation}><Trash2 size={17} /></button>
            </div>
          )}
        </header>

        <div className="messageStream">
          {!activeConversation && <div className="emptyState"><Bot size={34} /><h3>开始一次协作</h3><p>从左侧选择群组或联系人。</p></div>}
          {messages.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              <div className="messageMeta">
                <span>{message.sender_name}</span>
                <time>{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
                {message.pinned && <span className="pinBadge">长期上下文</span>}
              </div>
              {message.reply_to && <div className="quote">引用消息：{message.reply_to}</div>}
              <div className="markdownBody">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content || (message.streaming ? "正在连接 Agent…" : "")}
                </ReactMarkdown>
                {message.streaming && <span className="streamCursor" aria-label="正在生成" />}
              </div>
              {message.artifacts.length > 0 && (
                <div className="artifactGrid">
                  {message.artifacts.map((artifact) => (
                    <ArtifactCard key={artifact.id} artifact={artifact} conversationId={message.conversation_id} onExpand={() => setExpandedArtifact(artifact)}
                      onAccept={() => updateArtifact(message, artifact, "accepted")}
                      onDecline={() => updateArtifact(message, artifact, "declined")}
                      onResolveConflict={() => setConflictArtifact({ artifact, messageId: message.id })} />
                  ))}
                </div>
              )}
              {!activeConversation?.archived && (
                <div className="messageActions">
                  <button onClick={() => setReplyTo(message)}>回复</button>
                  {message.role === "agent" && <button title="重新生成" onClick={() => setDraft(`请重新生成这条回复：${message.content}`)}><RefreshCcw size={14} /></button>}
                  <button title="复制内容" onClick={() => navigator.clipboard.writeText(message.content)}><Copy size={14} /></button>
                  <button title="置顶为长期上下文" onClick={() => toggleMessagePin(message)}><Pin size={14} /></button>
                </div>
              )}
            </article>
          ))}
          {respondingConversationIds.includes(activeId) && !messages.some((message) => message.streaming) && (
            <article className="message typingMessage"><div className="messageMeta"><span>Agents</span><span>正在响应</span></div>
              <div className="typingDots"><span /><span /><span /></div></article>
          )}
        </div>

        <footer className={activeConversation?.archived ? "composer readonly" : "composer"}>
          {activeConversation?.archived ? (
            <div className="readonlyNotice"><Archive size={17} />该会话已归档，可以查看记录，但不能发表新消息。</div>
          ) : (
            <>
              {replyTo && <div className="replyBar">正在回复 {replyTo.sender_name}<button onClick={() => setReplyTo(null)}><X size={14} /></button></div>}
              {activeConversation && !activeConversation.archived && (
                <div className="quickComposerActions">
                  <button className="btnQuickDeploy" onClick={triggerDeploy} title="快速部署当前项目">
                    🚀 一键部署当前项目
                  </button>
                  <label className="composerParallelLabel" title="多 Agent 同时执行，并在聊天中并发输出">
                    <input type="checkbox" checked={isParallel} onChange={(e) => setParallel(e.target.checked)} />
                    ⚡ 并行调度
                  </label>
                </div>
              )}
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void sendMessage(); }}
                placeholder={activeConversation ? "输入消息，使用 @all 或 @Agent 指定协作者" : "请先选择会话"} disabled={!activeConversation} />
              <div className="composerActions"><span>⌘/Ctrl + Enter 发送 · 群聊由当前主 Agent 拆解并顺序调度</span>
                <button onClick={sendMessage} disabled={!activeConversation || !draft.trim()}><Send size={16} />发送</button></div>
            </>
          )}
        </footer>
      </section>

      <aside className="agentPanel">
        <div className="sidebarTabHeader">
          <button className={rightPanelTab === "members" ? "active" : ""} onClick={() => setRightPanelTab("members")}>
            Agent 成员
          </button>
          <button className={rightPanelTab === "history" ? "active" : ""} onClick={() => setRightPanelTab("history")} disabled={!activeConversation}>
            文件与历史
          </button>
        </div>

        {rightPanelTab === "members" ? (
          <>
            <div className="panelHeading">
              <div><p>当前会话</p><h2>Agent 成员</h2></div>
              <div className="panelHeadingActions">
                {activeConversation?.mode === "group" && !activeConversation.archived && (
                  <button title="从联系人添加 Agent" onClick={() => setAddMemberModalOpen(true)}><Plus size={17} /></button>
                )}
                <span>{activeMembers.length}</span>
              </div>
            </div>
            <div className="agentList">
              {activeMembers.map((agent) => (
                <button key={agent.id} className="agentCard" onClick={() => setSelectedMember(agent)}>
                  <div className="avatar">{agent.avatar}</div>
                  <div><strong>{agent.name}{activeConversation?.mode === "group" && agent.is_primary ? "（主Agent）" : ""}</strong>
                    <p>{agent.description || `${agent.provider} · ${agent.kind}`}</p>
                    <div className="tags">{agent.capability_tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div>
                </button>
              ))}
              {activeConversation && activeMembers.length === 0 && <div className="panelEmpty">当前会话还没有 Agent。</div>}
              {!activeConversation && <div className="panelEmpty">选择会话后，这里只显示该会话的成员。</div>}
            </div>
            <button className="createAgentButton" disabled={!activeConversation || activeConversation.archived} onClick={() => setAgentModalOpen(true)}>
              <Bot size={17} />创建新 Agent
            </button>
          </>
        ) : (
          <div className="workspaceHistoryPanel">
            <div className="panelSection">
              <h3>📂 项目文件列表</h3>
              <div className="fileList">
                {projectFiles.map((file) => (
                  <button key={file} className="fileRow" onClick={() => previewFile(file)}>
                    <Code2 size={14} />
                    <span>{file}</span>
                  </button>
                ))}
                {projectFiles.length === 0 && <div className="panelEmpty">工作区暂无文件。</div>}
              </div>
            </div>

            <div className="panelSection">
              <h3>⏳ 版本历史</h3>
              <div className="versionTimeline">
                {versionHistory.map((version) => (
                  <div key={version.id} className="versionCard">
                    <div className="versionBadge">{version.id.split('_')[0]}</div>
                    <div className="versionContent">
                      <p className="versionMsg">{version.message}</p>
                      <time className="versionTime">{new Date(version.timestamp).toLocaleString()}</time>
                      <button className="btnRevert" onClick={() => handleRevert(version.id)}>回滚到此</button>
                    </div>
                  </div>
                ))}
                {versionHistory.length === 0 && <div className="panelEmpty">暂无历史版本。</div>}
              </div>
            </div>
          </div>
        )}
      </aside>

      {expandedArtifact && (
        <ArtifactModal
          artifact={expandedArtifact}
          onClose={() => setExpandedArtifact(null)}
          onSelectLines={(filename, start, end, selectedText) => {
            setDraft((curr) => {
              const ref = [
                `请针对项目文件 ${filename} 的第 ${start}-${end} 行进行修改。`,
                "选中代码如下：",
                "```",
                selectedText,
                "```",
                "",
              ].join("\n");
              return ref + curr;
            });
            setExpandedArtifact(null);
          }}
        />
      )}
      {isAgentModalOpen && (
        <AgentModal form={agentForm} onChange={setAgentForm} onClose={() => setAgentModalOpen(false)} onCreate={createAgent} />
      )}
      {conflictArtifact && (
        <ConflictResolverModal
          artifact={conflictArtifact.artifact}
          messageId={conflictArtifact.messageId}
          conversationId={activeId}
          onClose={() => setConflictArtifact(null)}
          onResolved={() => {
            setConflictArtifact(null);
            void refreshData();
          }}
        />
      )}
      {selectedMember && activeConversation && (
        <MemberModal
          conversation={activeConversation}
          member={selectedMember}
          onChange={setSelectedMember}
          onClose={() => setSelectedMember(null)}
          onSave={saveMember}
        />
      )}
      {isAddMemberModalOpen && activeConversation && (
        <AddMemberModal
          contacts={contacts.filter((agent) => !activeMembers.some((member) => member.id === agent.id))}
          onAdd={addMember}
          onClose={() => setAddMemberModalOpen(false)}
        />
      )}
      {isRemoveContactModalOpen && (
        <RemoveContactsModal
          contacts={contacts}
          onRemove={removeContacts}
          onClose={() => setRemoveContactModalOpen(false)}
        />
      )}
    </main>
  );
}

function ConversationButton({ conversation, activeId, onClick }: { conversation: Conversation; activeId: string; onClick: (id: string) => void }) {
  return (
    <button className={conversation.id === activeId ? "conversation active" : "conversation"} onClick={() => onClick(conversation.id)}>
      <span className="conversationTitle">{conversation.pinned && <Pin size={12} />}{conversation.title}</span>
      <span className="conversationSnippet">{conversation.last_message || "还没有消息"}</span>
    </button>
  );
}

function AgentModal({ form, onChange, onClose, onCreate }: {
  form: typeof emptyAgentForm;
  onChange: (form: typeof emptyAgentForm) => void;
  onClose: () => void;
  onCreate: () => void;
}) {
  const complete = form.name.trim() && form.systemPrompt.trim() && form.description.trim();
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="agentModal">
        <header><div><p>自建 Agent</p><h2>创建新 Agent</h2></div><button onClick={onClose}><X size={18} /></button></header>
        <label>名称<input value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} placeholder="例如：产品研究员" /></label>
        <label>在本聊天会话中负责实现的功能<textarea value={form.description} onChange={(event) => onChange({ ...form, description: event.target.value })} placeholder="说明这个 Agent 擅长和负责什么" /></label>
        <label>System Prompt<textarea value={form.systemPrompt} onChange={(event) => onChange({ ...form, systemPrompt: event.target.value })} placeholder="该 Agent 每次工作前都要遵守的提示词" /></label>
        <fieldset><legend>添加范围</legend>
          <label className="scopeOption"><input type="radio" checked={form.scope === "conversation"} onChange={() => onChange({ ...form, scope: "conversation" })} />
            <span><strong>仅当前聊天</strong><small>只作为当前会话成员，不加入联系人。</small></span></label>
          <label className="scopeOption"><input type="radio" checked={form.scope === "contacts"} onChange={() => onChange({ ...form, scope: "contacts" })} />
            <span><strong>当前聊天 + 联系人</strong><small>以后可以从联系人再次发起单聊。</small></span></label>
        </fieldset>
        <footer><button className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={!complete} onClick={onCreate}><Bot size={16} />创建并添加</button></footer>
      </section>
    </div>
  );
}

function MemberModal({
  conversation,
  member,
  onChange,
  onClose,
  onSave
}: {
  conversation: Conversation;
  member: ConversationMember;
  onChange: (member: ConversationMember) => void;
  onClose: () => void;
  onSave: (member: ConversationMember) => void;
}) {
  const isGroup = conversation.mode === "group";
  const complete = member.name.trim() && member.system_prompt.trim() && (!isGroup || member.description.trim());
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="agentModal memberModal">
        <header>
          <div><p>{isGroup ? "当前群聊配置" : "单聊 Agent 信息"}</p><h2>{member.name}</h2></div>
          <button onClick={onClose}><X size={18} /></button>
        </header>
        <label>
          名称
          <input
            value={member.name}
            readOnly={!isGroup}
            onChange={(event) => onChange({ ...member, name: event.target.value })}
          />
        </label>
        {isGroup && (
          <label>
            在本聊天会话中负责实现的功能
            <textarea
              value={member.description}
              onChange={(event) => onChange({ ...member, description: event.target.value })}
            />
          </label>
        )}
        <label>
          System Prompt
          <textarea
            value={member.system_prompt}
            readOnly={!isGroup}
            onChange={(event) => onChange({ ...member, system_prompt: event.target.value })}
          />
        </label>
        {isGroup && (
          <label className="primaryAgentOption">
            <input
              type="checkbox"
              checked={member.is_primary}
              disabled={member.is_primary}
              onChange={(event) => onChange({ ...member, is_primary: event.target.checked })}
            />
            <span>
              <strong>将此 Agent 设为当前会话的主 Agent</strong>
              <small>保存后将与原主 Agent 交换职责和 System Prompt，仅影响本群聊。</small>
            </span>
          </label>
        )}
        <footer>
          <button className="secondary" onClick={onClose}>{isGroup ? "取消" : "关闭"}</button>
          {isGroup && <button className="primary" disabled={!complete} onClick={() => onSave(member)}>保存修改</button>}
        </footer>
      </section>
    </div>
  );
}

function AddMemberModal({
  contacts,
  onAdd,
  onClose
}: {
  contacts: Agent[];
  onAdd: (agent: Agent) => void;
  onClose: () => void;
}) {
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="agentModal addMemberModal">
        <header>
          <div><p>当前群聊</p><h2>从联系人添加 Agent</h2></div>
          <button onClick={onClose}><X size={18} /></button>
        </header>
        <div className="contactPicker">
          {contacts.map((agent) => (
            <button key={agent.id} className="contactPickerRow" onClick={() => onAdd(agent)}>
              <span className="miniAvatar">{agent.avatar}</span>
              <span><strong>{agent.name}</strong><small>{agent.description}</small></span>
              <Plus size={17} />
            </button>
          ))}
          {contacts.length === 0 && <div className="panelEmpty">联系人中的 Agent 都已加入当前群聊。</div>}
        </div>
        <footer><button className="secondary" onClick={onClose}>关闭</button></footer>
      </section>
    </div>
  );
}

function RemoveContactsModal({
  contacts,
  onRemove,
  onClose
}: {
  contacts: Agent[];
  onRemove: (agentIds: string[]) => void;
  onClose: () => void;
}) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const removableContacts = contacts.filter((agent) => !agent.is_builtin);

  function toggle(agentId: string) {
    setSelectedIds((current) =>
      current.includes(agentId)
        ? current.filter((id) => id !== agentId)
        : [...current, agentId]
    );
  }

  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="agentModal removeContactsModal">
        <header>
          <div><p>联系人管理</p><h2>删除联系人</h2></div>
          <button onClick={onClose}><X size={18} /></button>
        </header>
        <p className="modalHint">只会从联系人列表移除，不会删除历史聊天记录或现有群聊成员。</p>
        <div className="contactPicker">
          {contacts.map((agent) => (
            <label key={agent.id} className={`contactDeleteRow ${agent.is_builtin ? "protected" : ""}`}>
              <input
                type="checkbox"
                disabled={agent.is_builtin}
                checked={selectedIds.includes(agent.id)}
                onChange={() => toggle(agent.id)}
              />
              <span className="miniAvatar">{agent.avatar}</span>
              <span>
                <strong>{agent.name}</strong>
                <small>{agent.is_builtin ? "系统预设，不可删除" : agent.description}</small>
              </span>
            </label>
          ))}
          {removableContacts.length === 0 && (
            <div className="panelEmpty">当前没有可删除的用户自建联系人。</div>
          )}
        </div>
        <footer>
          <button className="secondary" onClick={onClose}>取消</button>
          <button
            className="dangerAction"
            disabled={selectedIds.length === 0}
            onClick={() => onRemove(selectedIds)}
          >
            <Trash2 size={16} />删除所选联系人
          </button>
        </footer>
      </section>
    </div>
  );
}

function mergeMessages(current: Message[], incoming: Message[]) {
  const messages = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) messages.set(message.id, message);
  return [...messages.values()].sort((left, right) => left.created_at.localeCompare(right.created_at));
}

function ArtifactCard({
  artifact,
  conversationId,
  onExpand,
  onAccept,
  onDecline,
  onResolveConflict
}: {
  artifact: Artifact;
  conversationId: string;
  onExpand: () => void;
  onAccept: () => void;
  onDecline: () => void;
  onResolveConflict?: () => void;
}) {
  if (artifact.type === "deploy") {
    let data: any = null;
    try {
      data = JSON.parse(artifact.content);
    } catch (e) {
      // ignore
    }
    if (data) {
      const isSuccess = data.status === "success";
      const isFailed = data.status === "failed";
      return (
        <section className={`artifact deployBoard ${data.status}`}>
          <div className="deployHeader">
            <span className="deployTitle">🚀 {artifact.title}</span>
            <span className={`deployBadge ${data.status}`}>
              {isSuccess ? "部署成功" : isFailed ? "部署失败" : `构建中 ${data.progress}%`}
            </span>
          </div>
          <div className="deployProgressWrapper">
            <div className="deployProgressBar" style={{ width: `${data.progress}%` }} />
          </div>
          <div className="deploySteps">
            {data.steps && data.steps.map((step: any, idx: number) => {
              let stepIcon = "⚪";
              if (step.status === "done") stepIcon = "🟢";
              else if (step.status === "running") stepIcon = "🟡";
              else if (step.status === "failed") stepIcon = "🔴";
              return (
                <div key={idx} className={`deployStep ${step.status}`}>
                  <span className="stepIcon">{stepIcon}</span>
                  <span className="stepName">{step.name}</span>
                </div>
              );
            })}
          </div>
          {data.logs && data.logs.length > 0 && (
            <div className="deployTerminal">
              <div className="terminalHeader">编译终端日志</div>
              <div className="terminalBody">
                {data.logs.map((log: string, idx: number) => (
                  <div key={idx} className="logLine">{log}</div>
                ))}
              </div>
            </div>
          )}
          <div className="deployActions">
            {isSuccess && data.url && (
              <a href={data.url} target="_blank" rel="noopener noreferrer" className="btnPreview">
                <Globe size={14} /> 立即预览
              </a>
            )}
            <a href={`/api/conversations/${conversationId}/download`} download className="btnDownload">
              <FileText size={14} /> 下载源码 ZIP
            </a>
          </div>
        </section>
      );
    }
  }

  if (artifact.type === "conflict") {
    let data: any[] = [];
    try {
      data = JSON.parse(artifact.content);
    } catch (e) {
      // ignore
    }
    const hasUnresolved = data.length > 0;
    return (
      <section className={`artifact conflictBoard ${hasUnresolved ? "unresolved" : "resolved"}`}>
        <div className="conflictHeader">
          <span className="conflictTitle">⚠️ 并行合并代码冲突</span>
          <span className="conflictBadge">
            {hasUnresolved ? `剩余 ${data.length} 个未解决` : "已全部解决"}
          </span>
        </div>
        <div className="conflictFilesList">
          {data.map((c: any, idx: number) => (
            <div key={idx} className="conflictFileRow">
              <span className="conflictFileIcon">📄</span>
              <span className="conflictFileName">{c.file}</span>
              <span className="conflictFileDetail">({c.agent_a_name} 💥 {c.agent_b_name})</span>
            </div>
          ))}
        </div>
        {hasUnresolved && (
          <button className="btnResolveConflict" onClick={onResolveConflict}>
            ⚡ 可视化解决冲突
          </button>
        )}
      </section>
    );
  }

  const Icon = artifact.type === "web_preview" ? Globe : artifact.type === "document" ? FileText : Code2;
  return (
    <section className={`artifact ${artifact.status}`}>
      <button className="artifactOpen" onClick={onExpand}><Icon size={18} /><span>{artifact.title}</span></button>
      <pre>{artifact.content}</pre>
      {artifact.type === "diff" && <div className="artifactActions"><button onClick={onAccept}><Check size={14} />Accept</button><button onClick={onDecline}><X size={14} />Decline</button></div>}
    </section>
  );
}

function ArtifactModal({
  artifact,
  onClose,
  onSelectLines
}: {
  artifact: Artifact;
  onClose: () => void;
  onSelectLines?: (filename: string, start: number, end: number, selectedText: string) => void;
}) {
  const [selectStart, setSelectStart] = React.useState<number | null>(null);
  const [selectEnd, setSelectEnd] = React.useState<number | null>(null);

  const isCode = artifact.type === "code" || artifact.type === "diff" || artifact.id.startsWith("file-preview-");
  const lines = isCode ? artifact.content.split("\n") : [];

  function handleLineClick(lineNum: number) {
    if (selectStart === null) {
      setSelectStart(lineNum);
      setSelectEnd(lineNum);
    } else {
      if (lineNum === selectStart) {
        setSelectStart(null);
        setSelectEnd(null);
      } else if (lineNum < selectStart) {
        setSelectStart(lineNum);
      } else {
        setSelectEnd(lineNum);
      }
    }
  }

  function handleSelectLines() {
    if (onSelectLines && selectStart !== null && selectEnd !== null) {
      const selectedText = lines.slice(selectStart - 1, selectEnd).join("\n");
      onSelectLines(artifact.title, selectStart, selectEnd, selectedText);
    }
  }

  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="modal artifactViewModal">
        <header>
          <h2>{artifact.title}</h2>
          <button onClick={onClose}><X size={18} /></button>
        </header>

        <div className="modalBody scrollable">
          {artifact.type === "web_preview" ? (
            <iframe title={artifact.title} srcDoc={artifact.content} />
          ) : isCode ? (
            <div className="codeViewerWrapper">
              {selectStart !== null && selectEnd !== null && (
                <div className="codeSelectionBar">
                  <span>已选择第 {selectStart} 至 {selectEnd} 行</span>
                  <button className="btnApplySelection" onClick={handleSelectLines}>
                    针对选中段落提问/修改
                  </button>
                </div>
              )}
              <div className="codeTable">
                {lines.map((line, idx) => {
                  const lineNum = idx + 1;
                  const isSelected = selectStart !== null && selectEnd !== null && lineNum >= selectStart && lineNum <= selectEnd;
                  return (
                    <div key={idx} className={`codeLineRow ${isSelected ? "selected" : ""}`} onClick={() => handleLineClick(lineNum)}>
                      <span className="codeLineNumber">{lineNum}</span>
                      <pre className="codeLineContent">{line || " "}</pre>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <pre>{artifact.content}</pre>
          )}
        </div>
      </section>
    </div>
  );
}

function ConflictResolverModal({
  artifact,
  messageId,
  conversationId,
  onClose,
  onResolved
}: {
  artifact: Artifact;
  messageId: string;
  conversationId: string;
  onClose: () => void;
  onResolved: () => void;
}) {
  const [currentFileIdx, setCurrentFileIdx] = React.useState(0);
  const [manualText, setManualText] = React.useState("");
  const [resolveMode, setResolveMode] = React.useState<"compare" | "manual">("compare");

  let conflicts: any[] = [];
  try {
    conflicts = JSON.parse(artifact.content);
  } catch (e) {
    // ignore
  }

  const currentConflict = conflicts[currentFileIdx];

  React.useEffect(() => {
    if (currentConflict) {
      setManualText(currentConflict.agent_a || "");
    }
  }, [currentConflict, resolveMode]);

  if (!currentConflict) {
    return (
      <div className="modalBackdrop" role="dialog" aria-modal="true">
        <section className="modal conflictResolverModal">
          <header><h2>解决合并冲突</h2><button onClick={onClose}><X size={18} /></button></header>
          <div className="panelEmpty">暂无冲突需要解决。</div>
        </section>
      </div>
    );
  }

  async function handleResolve(action: "keep_a" | "keep_b" | "manual") {
    try {
      await api.resolveConflict(conversationId, messageId, artifact.id, {
        file: currentConflict.file,
        action,
        manual_content: action === "manual" ? manualText : undefined
      });
      if (currentFileIdx + 1 < conflicts.length) {
        setCurrentFileIdx(currentFileIdx + 1);
        setResolveMode("compare");
      } else {
        alert("所有冲突已成功解决！");
        onResolved();
      }
    } catch (e) {
      alert(`解决冲突失败：${String(e)}`);
    }
  }

  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="modal conflictResolverModal">
        <header>
          <h2>解决合并冲突 ({currentFileIdx + 1} / {conflicts.length})</h2>
          <button onClick={onClose}><X size={18} /></button>
        </header>

        <div className="conflictResolverWorkspace">
          <div className="conflictFileInfo">
            <strong>冲突文件：</strong> <code>{currentConflict.file}</code>
          </div>

          <div className="resolveModeSelector">
            <button className={resolveMode === "compare" ? "active" : ""} onClick={() => setResolveMode("compare")}>
              差异对比并选择
            </button>
            <button className={resolveMode === "manual" ? "active" : ""} onClick={() => setResolveMode("manual")}>
              手动编辑合并
            </button>
          </div>

          {resolveMode === "compare" ? (
            <div className="conflictComparisonGrid">
              <div className="conflictCol">
                <h4>👈 {currentConflict.agent_a_name} 的修改版本</h4>
                <div className="codePreviewBox">
                  <pre>{currentConflict.agent_a || "(文件为空或被删除)"}</pre>
                </div>
                <button className="btnResolveKeep" onClick={() => handleResolve("keep_a")}>
                  采用 {currentConflict.agent_a_name} 版本
                </button>
              </div>

              <div className="conflictCol">
                <h4>👉 {currentConflict.agent_b_name} 的修改版本</h4>
                <div className="codePreviewBox">
                  <pre>{currentConflict.agent_b || "(文件为空或被删除)"}</pre>
                </div>
                <button className="btnResolveKeep" onClick={() => handleResolve("keep_b")}>
                  采用 {currentConflict.agent_b_name} 版本
                </button>
              </div>
            </div>
          ) : (
            <div className="conflictManualEdit">
              <h4>✍️ 手动合并编辑器</h4>
              <textarea
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="在此编写最终合并后的代码..."
              />
              <button className="btnResolveKeep btnResolveManual" onClick={() => handleResolve("manual")}>
                保存并使用此合并版本
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
