import { QqOutlined, WechatOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Form, Input, Typography } from "antd";
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
  const [form] = Form.useForm<LoginForm>();
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

  const clearFieldError = (name: keyof LoginForm) => {
    form.setFields([{ name, errors: [] }]);
  };

  return (
    <main
      className="dy-login-shell dy-login-quiet"
      style={{
        minHeight: "100vh",
      }}
    >
      <section className="dy-login-brand dy-login-stage">
        <div className="dy-login-brand-top">
          <img src="/logo.png" alt="同舟行" className="dy-login-small-mark" />
          <div className="dy-login-brand-name">
            <span>同舟行AI新媒体运营平台</span>
            <small>AI+Agent+运营</small>
          </div>
        </div>

        <div className="dy-login-hero-copy">
          <Typography.Title level={1} className="dy-login-title">
            从一句话，
            <br />
            到一整套执行
          </Typography.Title>
          <Typography.Paragraph className="dy-login-hero-support">
            主 Agent 理解目标，专家 Agent 接力推进，把新媒体运营变成可追踪的执行流。
          </Typography.Paragraph>
        </div>

        <div className="dy-login-bottom-note">
          <span>目标输入</span>
          <span>Agent 编排</span>
          <span>执行推进</span>
        </div>
      </section>

      <section
        className="dy-login-form-side"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
          background: "#ffffff",
        }}
      >
        <div className="dy-login-form-panel">
          <Typography.Title level={3} className="dy-login-form-title">
            进入平台
          </Typography.Title>
          <Form
            form={form}
            layout="vertical"
            onFinish={onFinish}
            requiredMark={false}
            validateTrigger="onSubmit"
            style={{ marginTop: 36 }}
          >
            <Form.Item
              name="email"
              label="邮箱"
              className="dy-login-form-item dy-login-field-email"
              rules={[{ required: true, message: "邮箱不能为空" }]}
            >
              <Input
                autoComplete="email"
                className="dy-login-control-input"
                size="large"
                onChange={() => clearFieldError("email")}
                style={{ borderRadius: 16, height: 48 }}
              />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              className="dy-login-form-item dy-login-field-password"
              rules={[{ required: true, message: "密码不能为空" }]}
            >
              <Input.Password
                autoComplete="current-password"
                className="dy-login-control-input"
                size="large"
                onChange={() => clearFieldError("password")}
                style={{ borderRadius: 16, height: 48 }}
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              block
              size="large"
              loading={loading}
              style={{ height: 48, borderRadius: 16, marginTop: 10 }}
            >
              进入系统
            </Button>
          </Form>
          <div className="dy-social-login" aria-label="第三方登录暂未开放">
            <div className="dy-social-divider">
              <span>其他方式登录</span>
            </div>
            <div className="dy-social-icons">
              <button type="button" disabled aria-label="通过微信登录">
                <WechatOutlined className="dy-social-brand-icon dy-social-wechat" />
              </button>
              <button type="button" disabled aria-label="通过QQ登录">
                <QqOutlined className="dy-social-brand-icon dy-social-qq" />
              </button>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
