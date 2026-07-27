import { ArrowUpOutlined, CloseOutlined, RobotOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

interface GlobalAgentLauncherProps {
  clientName?: string;
  projectName?: string;
  accountName?: string;
}

export function GlobalAgentLauncher({ clientName, projectName, accountName }: GlobalAgentLauncherProps) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<"discuss" | "task">("task");

  const submit = () => {
    const value = draft.trim();
    if (!value) return;
    navigate("/", { state: { agentDraft: value, agentMode: mode } });
    setOpen(false);
    setDraft("");
  };

  return (
    <div className="tz-agent-launcher">
      {open ? (
        <section className="tz-agent-launcher-panel" aria-label="运营大脑">
          <header>
            <span className="tz-agent-symbol"><RobotOutlined /></span>
            <span><strong>运营大脑</strong><small>当前工作空间已带入</small></span>
            <button type="button" aria-label="关闭运营大脑" onClick={() => setOpen(false)}><CloseOutlined /></button>
          </header>
          <div className="tz-agent-context-line">
            <span>{clientName ?? "未选客户"}</span>
            <span>{projectName ?? "未选项目"}</span>
            <span>{accountName ?? "未选账号"}</span>
          </div>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="告诉运营大脑你想完成什么"
            rows={4}
          />
          <footer>
            <div className="tz-agent-mode" aria-label="Agent 模式">
              <button type="button" className={mode === "discuss" ? "is-active" : ""} onClick={() => setMode("discuss")}>讨论</button>
              <button type="button" className={mode === "task" ? "is-active" : ""} onClick={() => setMode("task")}>正式任务</button>
            </div>
            <button type="button" className="tz-agent-submit" disabled={!draft.trim()} onClick={submit}>
              <ArrowUpOutlined />
              <span>交给运营大脑</span>
            </button>
          </footer>
        </section>
      ) : null}
      <button
        type="button"
        className="tz-agent-fab"
        aria-label="打开运营大脑"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <RobotOutlined />
        <span>运营大脑</span>
      </button>
    </div>
  );
}
