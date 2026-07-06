import {
  App as AntApp,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createUser, listUsers } from "../api/auth";
import { silverTagStyle } from "../theme/styles";
import type { CreateUserInput, Role, User } from "../types";

interface CreateForm {
  email: string;
  password: string;
  display_name: string;
  role: Role;
}

export default function Users() {
  const qc = useQueryClient();
  const { message } = AntApp.useApp();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<CreateForm>();

  const { data, isLoading } = useQuery({ queryKey: ["users"], queryFn: listUsers });

  const mutation = useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
    onSuccess: () => {
      message.success("用户已创建");
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: () => message.error("创建失败（邮箱可能已存在）"),
  });

  const columns: ColumnsType<User> = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "邮箱", dataIndex: "email" },
    { title: "姓名", dataIndex: "display_name" },
    {
      title: "角色",
      dataIndex: "role",
      width: 110,
      render: (role: Role) => (
        <Tag style={role === "admin" ? silverTagStyle : undefined}>
          {role === "admin" ? "管理员" : "成员"}
        </Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 90,
      render: (active: boolean) => (
        <Tag color={active ? "green" : "red"}>{active ? "启用" : "禁用"}</Tag>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          用户管理
        </Typography.Title>
        <Button type="primary" onClick={() => setOpen(true)}>
          新建用户
        </Button>
      </Space>

      <Card variant="borderless" styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data}
          pagination={false}
        />
      </Card>

      <Modal
        title="新建用户"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={mutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={{ role: "user" }}
          onFinish={(v) => mutation.mutate(v)}
        >
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email" }]}>
            <Input placeholder="user@example.com" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, min: 8, message: "至少 8 位" }]}
          >
            <Input.Password placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item name="display_name" label="姓名" rules={[{ required: true }]}>
            <Input placeholder="姓名" />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select
              options={[
                { value: "user", label: "成员" },
                { value: "admin", label: "管理员" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
