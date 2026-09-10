import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./layout/AppShell";
import { navigationItems } from "./layout/navigation";
import { LoginPage } from "./pages/LoginPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { NotesPage } from "./pages/NotesPage";
import { QaPage } from "./pages/QaPage";
import { RegisterPage } from "./pages/RegisterPage";
import { OverviewPage } from "./pages/OverviewPage";
import { InsightsPage } from "./pages/InsightsPage";
import { SearchPage } from "./pages/SearchPage";
import { LearningPage } from "./pages/LearningPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          {navigationItems.map((item) => (
            <Route
              key={item.path}
              path={item.path}
              element={
                item.path === "/overview" ? <OverviewPage /> :
                item.path === "/documents" ? <DocumentsPage /> :
                item.path === "/search" ? <SearchPage /> :
                item.path === "/learning" ? <LearningPage /> :
                item.path === "/qa" ? <QaPage /> : (
                  item.path === "/notes" ? <NotesPage /> :
                  <InsightsPage />
                )
              }
            />
          ))}
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/overview" replace />} />
    </Routes>
  );
}
