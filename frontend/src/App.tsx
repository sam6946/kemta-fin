import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import DashboardPage from "./pages/DashboardPage";
import OrganizationsPage from "./pages/OrganizationsPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ProjectsPage from "./pages/ProjectsPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import ResetPasswordPage from "./pages/ResetPasswordPage";
import SyncPage from "./pages/SyncPage";
import { SyncProvider } from "./sync/SyncProvider";

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="screen-center">Chargement…</div>;
  if (!user) return <Navigate to="/connexion" replace state={{ from: location.pathname }} />;
  return children;
}

export default function App() {
  return (
    <SyncProvider>
      <Routes>
      <Route path="/" element={<Navigate to="/tableau-de-bord" replace />} />
      <Route path="/connexion" element={<LoginPage />} />
      <Route path="/inscription" element={<RegisterPage />} />
      {/* MVP-017 — parcours « mot de passe oublié » */}
      <Route path="/mot-de-passe-oublie" element={<ForgotPasswordPage />} />
      <Route path="/reinitialiser-mot-de-passe" element={<ResetPasswordPage />} />
      <Route
        path="/tableau-de-bord"
        element={
          <RequireAuth>
            <DashboardPage />
          </RequireAuth>
        }
      />
      {/* Phase 3 — organisations, projets et membres */}
      <Route
        path="/organisations"
        element={
          <RequireAuth>
            <OrganizationsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/projets"
        element={
          <RequireAuth>
            <ProjectsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/projets/:id"
        element={
          <RequireAuth>
            <ProjectDetailPage />
          </RequireAuth>
        }
      />
      {/* Phase 5 — preuves terrain : lien direct pour le terrain (même écran, ancre dédiée) */}
      <Route
        path="/projets/:id/preuves"
        element={
          <RequireAuth>
            <ProjectDetailPage />
          </RequireAuth>
        }
      />
      {/* Phase 6 — suivi de la file hors ligne (MVP-009) */}
      <Route
        path="/synchronisation"
        element={
          <RequireAuth>
            <SyncPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/connexion" replace />} />
      </Routes>
    </SyncProvider>
  );
}
