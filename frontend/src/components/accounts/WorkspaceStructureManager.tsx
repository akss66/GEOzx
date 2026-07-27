import {
  CheckOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import { Button, Input, Select, Space, Tabs, Tag } from "antd";
import { useMemo, useState } from "react";

import type { Client, Project } from "../../types";

type WorkspaceStructureManagerProps = {
  clients: Client[];
  projects: Project[];
  pending: boolean;
  onCreateClient: (name: string) => void;
  onUpdateClient: (id: number, patch: { name?: string; status?: Client["status"] }) => void;
  onCreateProject: (input: { client_id: number; name: string; description?: string }) => void;
  onUpdateProject: (
    id: number,
    patch: { name?: string; description?: string; status?: Project["status"] },
  ) => void;
};

export default function WorkspaceStructureManager({
  clients,
  projects,
  pending,
  onCreateClient,
  onUpdateClient,
  onCreateProject,
  onUpdateProject,
}: WorkspaceStructureManagerProps) {
  const [clientName, setClientName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectClientId, setProjectClientId] = useState<number | undefined>(
    clients.find((client) => client.status === "active")?.id,
  );
  const [editingClient, setEditingClient] = useState<{ id: number; name: string } | null>(null);
  const [editingProject, setEditingProject] = useState<{ id: number; name: string } | null>(null);

  const clientNameById = useMemo(
    () => new Map(clients.map((client) => [client.id, client.name])),
    [clients],
  );
  const activeClientOptions = clients
    .filter((client) => client.status === "active")
    .map((client) => ({ label: client.name, value: client.id }));

  const clientPanel = (
    <div className="workspace-structure">
      <div className="workspace-structure__create">
        <Input
          value={clientName}
          onChange={(event) => setClientName(event.target.value)}
          placeholder="输入客户名称"
          maxLength={120}
          onPressEnter={() => {
            const name = clientName.trim();
            if (!name) return;
            onCreateClient(name);
            setClientName("");
          }}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={pending}
          disabled={!clientName.trim()}
          onClick={() => {
            const name = clientName.trim();
            if (!name) return;
            onCreateClient(name);
            setClientName("");
          }}
        >
          新建客户
        </Button>
      </div>
      <div className="workspace-structure__list">
        {clients.map((client) => {
          const editing = editingClient?.id === client.id;
          return (
            <div className="workspace-structure__row" key={client.id}>
              <div className="workspace-structure__identity">
                {editing ? (
                  <Input
                    value={editingClient.name}
                    maxLength={120}
                    onChange={(event) =>
                      setEditingClient({ id: client.id, name: event.target.value })
                    }
                  />
                ) : (
                  <>
                    <strong>{client.name}</strong>
                    <span>
                      {projects.filter((project) => project.client_id === client.id).length} 个项目
                    </span>
                  </>
                )}
              </div>
              <Space size={6}>
                <Tag color={client.status === "active" ? "green" : "default"}>
                  {client.status === "active" ? "启用" : "已归档"}
                </Tag>
                {editing ? (
                  <Button
                    size="small"
                    icon={<CheckOutlined />}
                    loading={pending}
                    onClick={() => {
                      const name = editingClient.name.trim();
                      if (name && name !== client.name) onUpdateClient(client.id, { name });
                      setEditingClient(null);
                    }}
                  >
                    保存
                  </Button>
                ) : (
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => setEditingClient({ id: client.id, name: client.name })}
                  >
                    改名
                  </Button>
                )}
                <Button
                  size="small"
                  danger={client.status === "active"}
                  icon={client.status === "active" ? <DeleteOutlined /> : <UndoOutlined />}
                  loading={pending}
                  onClick={() =>
                    onUpdateClient(client.id, {
                      status: client.status === "active" ? "archived" : "active",
                    })
                  }
                >
                  {client.status === "active" ? "归档" : "恢复"}
                </Button>
              </Space>
            </div>
          );
        })}
      </div>
    </div>
  );

  const projectPanel = (
    <div className="workspace-structure">
      <div className="workspace-structure__create workspace-structure__create--project">
        <Select
          value={projectClientId}
          onChange={setProjectClientId}
          options={activeClientOptions}
          placeholder="选择所属客户"
        />
        <Input
          value={projectName}
          onChange={(event) => setProjectName(event.target.value)}
          placeholder="输入项目名称"
          maxLength={120}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={pending}
          disabled={!projectName.trim() || projectClientId == null}
          onClick={() => {
            const name = projectName.trim();
            if (!name || projectClientId == null) return;
            onCreateProject({ client_id: projectClientId, name });
            setProjectName("");
          }}
        >
          新建项目
        </Button>
      </div>
      <div className="workspace-structure__list">
        {projects.map((project) => {
          const editing = editingProject?.id === project.id;
          return (
            <div className="workspace-structure__row" key={project.id}>
              <div className="workspace-structure__identity">
                {editing ? (
                  <Input
                    value={editingProject.name}
                    maxLength={120}
                    onChange={(event) =>
                      setEditingProject({ id: project.id, name: event.target.value })
                    }
                  />
                ) : (
                  <>
                    <strong>{project.name}</strong>
                    <span>{clientNameById.get(project.client_id ?? -1) ?? "未绑定客户"}</span>
                  </>
                )}
              </div>
              <Space size={6}>
                <Tag
                  color={
                    project.status === "active"
                      ? "green"
                      : project.status === "paused"
                        ? "gold"
                        : "default"
                  }
                >
                  {project.status === "active"
                    ? "进行中"
                    : project.status === "paused"
                      ? "已暂停"
                      : "已归档"}
                </Tag>
                {editing ? (
                  <Button
                    size="small"
                    icon={<CheckOutlined />}
                    loading={pending}
                    onClick={() => {
                      const name = editingProject.name.trim();
                      if (name && name !== project.name) onUpdateProject(project.id, { name });
                      setEditingProject(null);
                    }}
                  >
                    保存
                  </Button>
                ) : (
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => setEditingProject({ id: project.id, name: project.name })}
                  >
                    改名
                  </Button>
                )}
                {project.status === "active" ? (
                  <Button
                    size="small"
                    loading={pending}
                    onClick={() => onUpdateProject(project.id, { status: "paused" })}
                  >
                    暂停
                  </Button>
                ) : null}
                <Button
                  size="small"
                  danger={project.status !== "archived"}
                  icon={project.status === "archived" ? <UndoOutlined /> : <DeleteOutlined />}
                  loading={pending}
                  onClick={() =>
                    onUpdateProject(project.id, {
                      status: project.status === "archived" ? "active" : "archived",
                    })
                  }
                >
                  {project.status === "archived" ? "恢复" : "归档"}
                </Button>
              </Space>
            </div>
          );
        })}
      </div>
    </div>
  );

  return (
    <Tabs
      items={[
        { key: "clients", label: `客户 ${clients.length}`, children: clientPanel },
        { key: "projects", label: `项目 ${projects.length}`, children: projectPanel },
      ]}
    />
  );
}
