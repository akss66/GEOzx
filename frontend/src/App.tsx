import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { getMe } from "./api/auth";
import { isAuthenticationError, presentApiError } from "./api/errors";
import { APP_ROUTES, type AppPage } from "./appRoutes";
import { AppShell } from "./components/AppShell";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import { OperationalState } from "./components/ui";
import { useAuth } from "./stores/auth";

const Login = lazy(() => import("./pages/Login"));
const pageComponents: Record<AppPage, React.LazyExoticComponent<React.ComponentType>> = {
  brain: lazy(() => import("./pages/BrainHome")),
  agents: lazy(() => import("./pages/ExpertTeam")),
  tasks: lazy(() => import("./pages/PipelineBoard")),
  approvals: lazy(() => import("./pages/Approvals")),
  review: lazy(() => import("./pages/ReviewDashboard")),
  cost: lazy(() => import("./pages/Cost")),
  risks: lazy(() => import("./pages/Risks")),
  accounts: lazy(() => import("./pages/Accounts")),
  "account-data": lazy(() => import("./pages/AccountDataCenter")),
  knowledge: lazy(() => import("./pages/Knowledge")),
  config: lazy(() => import("./pages/Config")),
  models: lazy(() => import("./pages/ModelInfrastructure")),
  users: lazy(() => import("./pages/Users")),
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
    <Suspense fallback={<RouteLoadingState />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            {APP_ROUTES.filter((route) => !route.adminOnly).map((route) => {
              const Page = pageComponents[route.page];
              return route.index ? (
                <Route key="index" index element={<Page />} />
              ) : (
                <Route key={route.path} path={route.path} element={<Page />} />
              );
            })}
            <Route path="pipeline" element={<Navigate to="/tasks" replace />} />
            <Route element={<AdminRoute />}>
              {APP_ROUTES.filter((route) => route.adminOnly).map((route) => {
                const Page = pageComponents[route.page];
                return <Route key={route.path} path={route.path} element={<Page />} />;
              })}
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

function RouteLoadingState() {
  return (
    <div className="tz-bootstrap-state">
      <OperationalState
        kind="loading"
        title="正在打开页面"
        description="正在载入当前功能。"
      />
    </div>
  );
}
