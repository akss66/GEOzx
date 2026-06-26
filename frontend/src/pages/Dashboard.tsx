import { Card, Col, Row, Statistic, Typography } from "antd";

import { useAuth } from "../stores/auth";

export default function Dashboard() {
  const user = useAuth((s) => s.user);

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        工作台
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        欢迎，{user?.display_name}。流水线看板、复盘看板将在后续里程碑接入。
      </Typography.Paragraph>

      <Row gutter={16} style={{ marginTop: 8 }}>
        <Col span={6}>
          <Card variant="borderless">
            <Statistic title="运营项目" value={0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card variant="borderless">
            <Statistic title="矩阵账号" value={0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card variant="borderless">
            <Statistic title="进行中内容" value={0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card variant="borderless">
            <Statistic title="待审质量门" value={0} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
