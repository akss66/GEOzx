import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { login } from "../api/auth";
import { useAuth } from "../stores/auth";

interface LoginForm {
  email: string;
  password: string;
}

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
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <Card style={{ width: 380 }} variant="borderless">
        <div style={{ marginBottom: 24 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            DyFlow
          </Typography.Title>
          <Typography.Text type="secondary">运营工作台 · 登录</Typography.Text>
        </div>
        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ required: true, message: "请输入邮箱" }]}
          >
            <Input prefix={<UserOutlined />} placeholder="admin@dyflow.local" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="••••••••" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
