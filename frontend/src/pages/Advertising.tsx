import { RiseOutlined } from "@ant-design/icons";

import { ModulePlaceholder } from "../components/ModulePlaceholder";

export default function Advertising() {
  return (
    <ModulePlaceholder
      title="投流"
      subtitle="与内容生产并行的投放运营 · 千川计划 / 人群定向 / ROI / 自动投放"
      phase="M3"
      icon={<RiseOutlined />}
      features={[
        "巨量千川投放计划生成（投流 Agent）",
        "人群定向与出价策略",
        "自动投放规则引擎（关停/追爆/扩量）",
        "ROI 追踪（接复盘看板）",
        "大额投放（日耗 > ¥2000）强制人工质量门 Gate6",
      ]}
    />
  );
}
