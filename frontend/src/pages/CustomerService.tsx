import { CustomerServiceOutlined } from "@ant-design/icons";

import { ModulePlaceholder } from "../components/ModulePlaceholder";

export default function CustomerService() {
  return (
    <ModulePlaceholder
      title="客服"
      subtitle="贯穿全生命周期的用户运营 · 评论回复 / 负面管理 / 私域导流 / 需求反哺"
      phase="M2"
      icon={<CustomerServiceOutlined />}
      features={[
        "评论抓取与智能回复（客服 Agent）",
        "负面舆情识别与处理",
        "私域导流（合规话术，接知识库话术库）",
        "高意向用户识别与跟进",
        "用户需求报告反哺编导选题",
        "评论情感分析（接复盘看板）",
      ]}
    />
  );
}
