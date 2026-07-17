import { ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Button, Segmented, Skeleton } from "antd";
import { useState } from "react";

import { getCostOverview, getTechnicalCostOverview } from "../api/costs";
import { BusinessCostView } from "../components/costs/BusinessCostView";
import { TechnicalCostView } from "../components/costs/TechnicalCostView";
import { useAuth } from "../stores/auth";
import { useCurrentWorkspace } from "../stores/currentWorkspace";

type CostMode = "business" | "technical";

export default function Cost() {
  const { clientId, projectId } = useCurrentWorkspace();
  const isAdmin = useAuth((state) => state.user?.role === "admin");
  const [mode, setMode] = useState<CostMode>("business");
  const [days, setDays] = useState(30);

  const businessQuery = useQuery({
    queryKey: ["cost-overview", clientId, projectId, days],
    queryFn: () => getCostOverview({ clientId: clientId!, projectId, days }),
    enabled: mode === "business" && clientId != null,
  });
  const technicalQuery = useQuery({
    queryKey: ["cost-technical", days],
    queryFn: () => getTechnicalCostOverview(days),
    enabled: mode === "technical" && isAdmin,
  });

  return (
    <main className="cost-workspace">
      <header className="cost-workspace__header">
        <div>
          <span>运营投入</span>
          <h1>使用成本</h1>
          <p>
            {mode === "business"
              ? "用预算、任务和专家归因判断每一笔运营投入。"
              : "查看模型基础设施的调用、延迟、失败与兜底情况。"}
          </p>
        </div>
        <div className="cost-workspace__controls">
          {isAdmin ? (
            <Segmented
              value={mode}
              options={[
                { label: "运营成本", value: "business" },
                { label: "技术运行", value: "technical" },
              ]}
              onChange={(value) => setMode(value as CostMode)}
            />
          ) : null}
          <Segmented
            value={days}
            options={[
              { label: "近 7 天", value: 7 },
              { label: "近 30 天", value: 30 },
              { label: "近 90 天", value: 90 },
            ]}
            onChange={(value) => setDays(Number(value))}
          />
        </div>
      </header>

      {mode === "business" ? (
        clientId == null ? (
          <ScopeGate />
        ) : businessQuery.isLoading ? (
          <CostSkeleton />
        ) : businessQuery.isError || !businessQuery.data ? (
          <CostError onRetry={() => void businessQuery.refetch()} />
        ) : (
          <BusinessCostView overview={businessQuery.data} />
        )
      ) : technicalQuery.isLoading ? (
        <CostSkeleton />
      ) : technicalQuery.isError || !technicalQuery.data ? (
        <CostError onRetry={() => void technicalQuery.refetch()} />
      ) : (
        <TechnicalCostView overview={technicalQuery.data} />
      )}
    </main>
  );
}

function ScopeGate() {
  return (
    <section className="cost-gate">
      <span>01</span>
      <div>
        <h2>先选择一个客户查看成本</h2>
        <p>成本严格跟随客户与项目上下文，不会展示组织范围的混合数据。</p>
      </div>
    </section>
  );
}

function CostSkeleton() {
  return <Skeleton active paragraph={{ rows: 12 }} className="cost-skeleton" />;
}

function CostError({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="cost-error" role="alert">
      <div><strong>成本数据加载失败</strong><span>请检查服务状态后重试。</span></div>
      <Button icon={<ReloadOutlined />} onClick={onRetry}>重新加载</Button>
    </section>
  );
}
