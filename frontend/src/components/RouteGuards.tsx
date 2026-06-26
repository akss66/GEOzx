import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "../stores/auth";

/** 未登录跳转到登录页。 */
export function ProtectedRoute() {
  const token = useAuth((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

/** 非 admin 跳回工作台（菜单已隐藏，这里兜底防直接访问 URL）。 */
export function AdminRoute() {
  const user = useAuth((s) => s.user);
  if (user && user.role !== "admin") return <Navigate to="/" replace />;
  return <Outlet />;
}
