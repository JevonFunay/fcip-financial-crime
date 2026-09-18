import { useQuery } from "@tanstack/react-query";

import { apiClient } from "./api/client";

function useBackendHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const response = await apiClient.get<{ status: string }>("/health");
      return response.data;
    },
  });
}

// Placeholder shell for Tahap 1 — proves the frontend/backend/db containers
// are wired together end to end. Real pages (login, alert queue, alert
// detail, case detail) land in a later stage.
export default function App() {
  const { data, isLoading, isError } = useBackendHealth();

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>Financial Crime Intelligence Platform</h1>
      <p>
        Backend status:{" "}
        {isLoading ? "checking..." : isError ? "unreachable" : data?.status}
      </p>
    </main>
  );
}
