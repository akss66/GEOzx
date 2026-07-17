import { FolderOpenOutlined, UserSwitchOutlined } from "@ant-design/icons";

import { ContentWorkspaceView } from "../components/content/ContentWorkspace";
import { PageHeader } from "../components/ui";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import "../styles/content-workspace.css";

export default function PipelineBoard() {
  const projectId = useCurrentWorkspace((state) => state.projectId);
  const accountId = useCurrentWorkspace((state) => state.accountId);

  return (
    <div className="content-production-page">
      <PageHeader
        title="内容生产"
        subtitle="在同一条内容里推进正式成果、素材、版本、审批与发布准备"
      />
      {projectId == null ? (
        <section className="content-context-required">
          <FolderOpenOutlined />
          <h2>先选择一个项目</h2>
          <p>内容、成果、素材和审批都必须属于明确项目。请从左上角切换客户与项目。</p>
        </section>
      ) : (
        <>
          {accountId == null ? (
            <section className="content-context-banner">
              <UserSwitchOutlined />
              <div>
                <strong>尚未选择当前账号</strong>
                <p>可以查看项目历史内容；创建新内容前，请从顶部选择抖音账号。</p>
              </div>
            </section>
          ) : null}
          <ContentWorkspaceView projectId={projectId} accountId={accountId} />
        </>
      )}
    </div>
  );
}
