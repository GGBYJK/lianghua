import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import "antd/dist/reset.css";

import { PlatformApp } from "./platform/PlatformApp";


const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 1000 },
    mutations: { retry: 0 },
  },
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PlatformApp />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

