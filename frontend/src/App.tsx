import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { AlertQueuePage } from "./pages/AlertQueuePage";
import { AuditPage } from "./pages/AuditPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { LabPage } from "./pages/LabPage";
import { LoginPage } from "./pages/LoginPage";
import { ModelFlagsPage } from "./pages/ModelFlagsPage";
import { OverviewPage } from "./pages/OverviewPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/alerts" element={<AlertQueuePage />} />
          <Route path="/alerts/:id" element={<AlertDetailPage />} />
          <Route path="/cases/:id" element={<CaseDetailPage />} />
          <Route path="/model-flags" element={<ModelFlagsPage />} />
          <Route path="/lab" element={<LabPage />} />
          <Route path="/audit" element={<AuditPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
