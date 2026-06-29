import { Spin } from "antd";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { getMe } from "./api/auth";
import { AppShell } from "./components/AppShell";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import Accounts from "./pages/Accounts";
import Advertising from "./pages/Advertising";
import Approvals from "./pages/Approvals";
import Config from "./pages/Config";
import Cost from "./pages/Cost";
import CustomerService from "./pages/CustomerService";
import Dashboard from "./pages/Dashboard";
import Knowledge from "./pages/Knowledge";
import Login from "./pages/Login";
import PipelineBoard from "./pages/PipelineBoard";
import ReviewDashboard from "./pages/ReviewDashboard";
import Users from "./pages/Users";
import { useAuth } from "./stores/auth";

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
          <Route index element={<Dashboard />} />
          <Route path="pipeline" element={<PipelineBoard />} />
          <Route path="approvals" element={<Approvals />} />
          <Route path="customer-service" element={<CustomerService />} />
          <Route path="advertising" element={<Advertising />} />
          <Route path="review" element={<ReviewDashboard />} />
          <Route path="cost" element={<Cost />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route element={<AdminRoute />}>
            <Route path="config" element={<Config />} />
            <Route path="users" element={<Users />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
