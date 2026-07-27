import {
  AimOutlined,
  BarChartOutlined,
  BgColorsOutlined,
  CustomerServiceOutlined,
  FileTextOutlined,
  NotificationOutlined,
  ScissorOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";

import type { AgentCode } from "../../types";

const EXPERT_ICONS: Partial<Record<AgentCode, ReactNode>> = {
  "01-positioning": <AimOutlined />,
  "02-content-director": <FileTextOutlined />,
  "03-art-director": <BgColorsOutlined />,
  "04-video-creator": <VideoCameraOutlined />,
  "05-editor": <ScissorOutlined />,
  "06-operator": <BarChartOutlined />,
  "07-advertiser": <NotificationOutlined />,
  "08-customer-service": <CustomerServiceOutlined />,
};

const MAIN_AGENT_AVATAR_SRC = "/main-agent-avatar.png";

export function AgentAvatar({
  code,
  className = "",
  label,
}: {
  code: AgentCode;
  className?: string;
  label?: string;
}) {
  const classes = ["tz-agent-avatar", className].filter(Boolean).join(" ");
  if (code === "00-decision") {
    return (
      <span className={`${classes} is-main-agent`} role="img" aria-label={label ?? "主 Agent"}>
        <img src={MAIN_AGENT_AVATAR_SRC} alt="" />
      </span>
    );
  }

  return (
    <span
      className={`${classes} is-expert`}
      data-agent-code={code}
      role="img"
      aria-label={label ?? "专家 Agent"}
    >
      {EXPERT_ICONS[code] ?? <AimOutlined />}
    </span>
  );
}
