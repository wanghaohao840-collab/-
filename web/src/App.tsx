import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./layout/AppShell";
import { navigationItems } from "./layout/navigation";
import { LoginPage } from "./pages/LoginPage";
import { MigrationPage } from "./pages/MigrationPage";
import { RegisterPage } from "./pages/RegisterPage";

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
              element={<MigrationPage heading={item.heading} />}
            />
          ))}
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/overview" replace />} />
    </Routes>
  );
}
