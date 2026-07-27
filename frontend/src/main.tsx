/* eslint-disable react-refresh/only-export-components -- 入口文件，Root 仅用于注入主题/Providers */
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "@fontsource-variable/geist/wght.css";
import "@fontsource-variable/noto-sans-sc/wght.css";

import App from "./App";
import { buildTheme } from "./theme/tokens";
import "./index.css";
import "./styles/foundation.css";
import "./styles/app-shell.css";
import "./styles/brain-v2.css";
import "./styles/accounts-v2.css";
import "./styles/content-workspace.css";
import "./styles/publishing.css";
import "./styles/approval-workbench.css";
import "./styles/review-dashboard.css";
import "./styles/expert-studio.css";
import "./styles/expert-admin.css";
import "./styles/model-infrastructure.css";
import "./styles/knowledge-studio.css";
import "./styles/cost-workspace.css";
import "./styles/user-workspace.css";
import "./styles/high-fidelity-system.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function Root() {
  return (
    <ConfigProvider locale={zhCN} theme={buildTheme()}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
