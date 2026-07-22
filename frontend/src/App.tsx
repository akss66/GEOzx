import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { getMe } from "./api/auth";
import { isAuthenticationError, presentApiError } from "./api/errors";
import { APP_ROUTES, type AppPage } from "./appRoutes";
import { AppShell } from "./components/AppShell";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import { OperationalState } from "./components/ui";
import Accounts from "./pages/Accounts";
import AccountDataCenter from "./pages/AccountDataCenter";
import Advertising from "./pages/Advertising";
import Approvals from "./pages/Approvals";
import BrainHome from "./pages/BrainHome";
import Config from "./pages/Config";
import Cost from "./pages/Cost";
import CustomerService from "./pages/CustomerService";
import ExpertTeam from "./pages/ExpertTeam";
import Knowledge from "./pages/Knowledge";
import Login from "./pages/Login";
import ModelInfrastructure from "./pages/ModelInfrastructure";
import PipelineBoard from "./pages/PipelineBoard";
import ReviewDashboard from "./pages/ReviewDashboard";
import Risks from "./pages/Risks";
import Users from "./pages/Users";
import { useAuth } from "./stores/auth";

const pageElements: Record<AppPage, JSX.Element> = {
  brain: <BrainHome />,
  agents: <ExpertTeam />,
  tasks: <PipelineBoard />,
  approvals: <Approvals />,
  "customer-service": <CustomerService />,
  advertising: <Advertising />,
  review: <ReviewDashboard />,
  cost: <Cost />,
  risks: <Risks />,
  accounts: <Accounts />,
  "account-data": <AccountDataCenter />,
  knowledge: <Knowledge />,
  config: <Config />,
  models: <ModelInfrastructure />,
  users: <Users />,
};

export default function App() {
  const { token, user, setUser, logout } = useAuth();
  const [booting, setBooting] = useState<boolean>(!!token && !user);
  const [bootstrapError, setBootstrapError] = useState<unknown>(null);

  const bootstrap = () => {
    if (!token || user) return;
    setBooting(true);
    setBootstrapError(null);
    getMe()
      .then(setUser)
      .catch((error: unknown) => {
        if (isAuthenticationError(error)) {
          logout();
          return;
        }
        setBootstrapError(error);
      })
      .finally(() => setBooting(false));
  };

  useEffect(() => {
    bootstrap();
    // 仅首次挂载 bootstrap
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (booting) {
    return (
      <div className="tz-bootstrap-state">
        <OperationalState
          kind="loading"
          title="正在进入工作区"
          description="正在校验登录状态并读取你的客户、项目与账号权限。"
        />
      </div>
    );
  }

  if (bootstrapError) {
    const failure = presentApiError(
      bootstrapError,
      "系统暂时无法完成登录校验，请稍后重试。",
    );
    return (
      <div className="tz-bootstrap-state">
        <OperationalState
          kind="error"
          title="工作区暂时无法打开"
          description={`${failure.message} 你的登录信息仍然保留。`}
          diagnostic={failure.diagnostic}
          actionLabel="重新连接"
          onAction={bootstrap}
        />
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          {APP_ROUTES.filter((route) => !route.adminOnly).map((route) =>
            route.index ? (
              <Route key="index" index element={pageElements[route.page]} />
            ) : (
              <Route key={route.path} path={route.path} element={pageElements[route.page]} />
            ),
          )}
          <Route element={<AdminRoute />}>
            {APP_ROUTES.filter((route) => route.adminOnly).map((route) => (
              <Route key={route.path} path={route.path} element={pageElements[route.page]} />
            ))}
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
