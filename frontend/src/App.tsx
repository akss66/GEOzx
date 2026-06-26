import { Spin } from "antd";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { getMe } from "./api/auth";
import { AppShell } from "./components/AppShell";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import { useAuth } from "./stores/auth";

export default function App() {
  const { token, user, setUser, logout } = useAuth();
  // 有令牌但还没拉到用户信息时，先 bootstrap 一次 /me
  const [booting, setBooting] = useState<boolean>(!!token && !user);

  useEffect(() => {
    if (token && !user) {
      getMe()
        .then(setUser)
        .catch(() => logout())
        .finally(() => setBooting(false));
    }
    // 仅在首次挂载时 bootstrap
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
          <Route element={<AdminRoute />}>
            <Route path="users" element={<Users />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
