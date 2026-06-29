import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Form, Input, Typography } from "antd";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { login } from "../api/auth";
import { useAuth } from "../stores/auth";

interface LoginForm {
  email: string;
  password: string;
}

const HIGHLIGHTS = [
  "8 个 AI Agent 协同，覆盖定位到投放全链路",
  "交付物版本化落库，上游产出自动触发下游",
  "关键风险环节人工把关，其余自动通过",
  "富可视化复盘，数据 → 洞察 → 优化闭环",
];

export default function Login() {
  const { token, setAuth } = useAuth();
  const navigate = useNavigate();
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  if (token) return <Navigate to="/" replace />;

  const onFinish = async (values: LoginForm) => {
    setLoading(true);
    try {
      const data = await login(values.email, values.password);
      setAuth(data.access_token, data.user);
      navigate("/");
    } catch {
      message.error("邮箱或密码错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", background: "var(--dy-canvas)" }}>
      {/* 左侧品牌区 */}
      <div
        style={{
          flex: "1 1 0",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 8%",
          borderRight: "1px solid var(--dy-border-subtle)",
          background:
            "radial-gradient(120% 80% at 0% 0%, rgba(91,140,255,0.10), transparent 60%)",
        }}
        className="dy-login-brand"
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 28 }}>
          <img
            src="/logo.png"
            alt="同舟行"
            style={{ width: 36, height: 36, borderRadius: 9, objectFit: "contain" }}
          />
          <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: 0.4 }}>同舟行</span>
        </div>
        <Typography.Title level={2} style={{ margin: 0, maxWidth: 460, fontWeight: 650 }}>
          同舟行 · 自媒体 AI 运营系统
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ fontSize: 15, marginTop: 12, maxWidth: 420 }}>
          把全流程交给协同的 AI Agent，团队只需在一个工作台里看数据、做决策、卡审核。
        </Typography.Paragraph>
        <ul style={{ listStyle: "none", padding: 0, margin: "20px 0 0", maxWidth: 420 }}>
          {HIGHLIGHTS.map((h) => (
            <li
              key={h}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "center",
                color: "var(--dy-muted)",
                fontSize: 14,
                padding: "7px 0",
              }}
            >
              <span
                style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--dy-accent)", flex: "none" }}
              />
              {h}
            </li>
          ))}
        </ul>
      </div>

      {/* 右侧登录表单 */}
      <div
        style={{
          flex: "0 0 480px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
        }}
      >
        <div style={{ width: 340 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            登录工作台
          </Typography.Title>
          <Typography.Text type="secondary">运营团队成员入口</Typography.Text>
          <Form layout="vertical" onFinish={onFinish} requiredMark={false} style={{ marginTop: 24 }}>
            <Form.Item name="email" label="邮箱" rules={[{ required: true, message: "请输入邮箱" }]}>
              <Input prefix={<UserOutlined />} placeholder="admin@dyflow.local" size="large" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="••••••••" size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}>
              登录
            </Button>
          </Form>
          <div
            style={{
              marginTop: 16,
              padding: "10px 12px",
              background: "var(--dy-elevated)",
              border: "1px solid var(--dy-border-subtle)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--dy-faint)",
            }}
          >
            演示账号：admin@dyflow.local / admin12345
          </div>
        </div>
      </div>
    </div>
  );
}
