import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { AlertQueuePage } from "./pages/AlertQueuePage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { LoginPage } from "./pages/LoginPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/alerts" replace />} />
          <Route path="/alerts" element={<AlertQueuePage />} />
          <Route path="/alerts/:id" element={<AlertDetailPage />} />
          <Route path="/cases/:id" element={<CaseDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/alerts" replace />} />
    </Routes>
  );
}
