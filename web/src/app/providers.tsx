"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { AuthProvider, clearSession } from "@/lib/auth";

// A 401 means the token expired or was revoked (password change, disabled account, signed out
// elsewhere): end the session instead of leaving every page in an error state.
function onError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) clearSession();
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError }),
        mutationCache: new MutationCache({ onError }),
        defaultOptions: {
          // Live updates arrive over the WebSocket; polling is only a fallback.
          queries: { staleTime: 10_000, refetchInterval: 30_000, retry: 1 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
