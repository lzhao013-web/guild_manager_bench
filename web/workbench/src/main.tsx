import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@radix-ui/themes/styles.css";
import "./fonts.css";
import "./styles.css";
import App from "./App";

const client = new QueryClient({
    defaultOptions: {
        queries: { retry: 1, staleTime: 500, networkMode: "always" },
    },
});
createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <QueryClientProvider client={client}>
            <App />
        </QueryClientProvider>
    </StrictMode>,
);
