import { Spin } from "antd";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { getMe } from "./api/auth";
import { APP_ROUTES, type AppPage } from "./appRoutes";
import { AppShell } from "./components/AppShell";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import Accounts from "./pages/Accounts";
import Advertising from "./pages/Advertising";
import Approvals from "./pages/Approvals";
import BrainHome from "./pages/BrainHome";
import Config from "./pages/Config";
import Cost from "./pages/Cost";
import CustomerService from "./pages/CustomerService";
import ExpertTeam from "./pages/ExpertTeam";
import Knowledge from "./pages/Knowledge";
import Login from "./pages/Login";
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
  knowledge: <Knowledge />,
  config: <Config />,
  users: <Users />,
};

export default function App() {
  const { token, user, setUser, logout } = useAuth();
  const [booting, setBooting] = useState<boolean>(!!token && !user);

  useEffect(() => {
    if (token && !user) {
      getMe()
        .then(setUser)
        .catch(() => logout())
        .finally(() => setBooting(false));
    }
    // 仅首次挂载 bootstrap
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (booting) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <Spin size="large" />
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
