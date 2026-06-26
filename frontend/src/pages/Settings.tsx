import { Card, Result, Typography } from "antd";

export default function Settings() {
  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        系统配置
      </Typography.Title>
      <Card variant="borderless">
        <Result
          status="info"
          title="即将上线"
          subTitle="API Key、模型配置、质量门策略、集成配置将在后续里程碑接入（仅管理员可见）。"
        />
      </Card>
    </div>
  );
}
