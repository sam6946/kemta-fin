/** Barre de navigation commune aux écrans connectés : accès rapides et cloche. */

import { NavLink } from "react-router-dom";

import NotificationBell from "./NotificationBell";

export default function AppBar() {
  return (
    <nav className="appbar" aria-label="Navigation principale">
      <NavLink to="/tableau-de-bord">Accueil</NavLink>
      <NavLink to="/espace">Mon espace</NavLink>
      <NavLink to="/projets">Projets</NavLink>
      <span className="appbar-spacer" />
      <NotificationBell />
    </nav>
  );
}
