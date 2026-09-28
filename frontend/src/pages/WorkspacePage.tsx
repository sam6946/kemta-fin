/** Écran « Mon espace » : l'espace de travail seul, sans le reste du tableau de bord d'accueil. */

import Workspace from "../components/Workspace";

export default function WorkspacePage() {
  return (
    <div className="dashboard">
      <Workspace />
    </div>
  );
}
