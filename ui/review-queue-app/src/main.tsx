import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App.tsx";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // This is a local, single-user demo tool talking to a same-machine
      // FastAPI process -- refetch-on-focus/reconnect churn adds nothing
      // here and just makes network activity harder to reason about while
      // testing. Explicit invalidation after a review submission is the
      // one refetch trigger that actually matters.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
