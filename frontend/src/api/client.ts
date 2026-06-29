import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
export const TOKEN_KEY = "dyflow_token";

export const api = axios.create({ baseURL: API_BASE });

// 请求拦截：附带 Bearer 令牌
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截：401 时清除本地令牌（路由层负责跳登录）
api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
    }
    return Promise.reject(error);
  },
);
