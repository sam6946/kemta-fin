# Matrice des rôles et permissions (Phase 0)

**Principe : le backend est l'autorité.** Le frontend masque/désactive les actions non autorisées
pour l'ergonomie, mais chaque endpoint vérifie ses permissions côté serveur (DRF
`permission_classes` + `get_queryset()` filtré par appartenance).

## 1. Les neuf rôles

| Code | Rôle | Portée | Description |
|---|---|---|---|
| `PLATFORM_ADMIN` | Administrateur plateforme | Globale | Exploitation, configuration, lecture des journaux techniques |
| `ORG_OWNER` | Promoteur / propriétaire d'organisation | Organisation | Crée l'organisation, ses projets, ses membres |
| `PROJECT_OWNER` | Maître d'ouvrage | Projet | Pilote le projet, valide les jalons |
| `ENGINEER` | Ingénieur / bureau d'études | Projet | Planning, jalons, tâches, validation technique |
| `CONTRACTOR` | Entreprise / PME de travaux | Projet | Exécution, capture de preuves, dépenses |
| `FIELD_AGENT` | Agent terrain / chef de chantier | Projet | Capture de preuves principalement |
| `VALIDATOR` | Contrôleur / validateur | Projet | Valide, rejette ou signale les preuves |
| `FINANCE` | Gestionnaire financier | Projet | Budget, dépenses, paiements |
| `INVESTOR` | Investisseur / bailleur | Projet | Lecture seule + tableau de bord investisseur |

Un utilisateur a **un rôle global** (`User.role`) et **un rôle par projet** (`ProjectMember.role`).
Le rôle par projet est prioritaire sur le rôle global pour l'accès aux données d'un projet.
Un même utilisateur peut être `INVESTOR` sur un projet et `ENGINEER` sur un autre.

## 2. Capacités additionnelles par projet

`ProjectMember.can_validate_evidence` et `ProjectMember.can_manage_finance` permettent
d'affiner sans créer de rôle : elles n'ajoutent des droits que sur le projet concerné, et leur
modification est journalisée (`MEMBER_ROLE_CHANGED`).

## 3. Matrice — organisations et projets

| Action | PLATFORM_ADMIN | ORG_OWNER | PROJECT_OWNER | ENGINEER | CONTRACTOR | FIELD_AGENT | VALIDATOR | FINANCE | INVESTOR |
|---|---|---|---|---|---|---|---|---|---|
| Créer une organisation | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Modifier son organisation | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Créer un projet | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Modifier un projet | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Archiver un projet | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Voir un projet (autorisé) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Ajouter / retirer un membre | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Changer un rôle | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

## 4. Matrice — suivi opérationnel

| Action | PLATFORM_ADMIN | ORG_OWNER | PROJECT_OWNER | ENGINEER | CONTRACTOR | FIELD_AGENT | VALIDATOR | FINANCE | INVESTOR |
|---|---|---|---|---|---|---|---|---|---|
| Créer / modifier jalon | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Clôturer un jalon | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Créer / modifier tâche | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Marquer une tâche terminée | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Voir planning / Gantt | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## 5. Matrice — preuves terrain

| Action | PLATFORM_ADMIN | ORG_OWNER | PROJECT_OWNER | ENGINEER | CONTRACTOR | FIELD_AGENT | VALIDATOR | FINANCE | INVESTOR |
|---|---|---|---|---|---|---|---|---|---|
| Capturer une preuve | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Voir les preuves | ✅ | ✅ | ✅ | ✅ | ✅ | ses preuves | ✅ | ✅ | ✅ (validées) |
| Valider / rejeter / signaler | ✅ | ✅ | ✅ | ✅ (si `can_validate_evidence`) | ❌ | ❌ | ✅ | ❌ | ❌ |
| Rouvrir une preuve | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Voir l'historique de validation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

*Implémentation (phase 5)* : `capture_evidence` et `validate_evidence` sont exposés par la
séquence `permissions` du projet (`PROJECT_PERMISSION_KEYS`) et recalculés **preuve par preuve**
par l'API (`{ validate_evidence, cannot_validate_own, can_see_location }`) : l'écran n'affiche que
les décisions réellement autorisées. Personne ne valide sa propre preuve — l'administrateur
plateforme (supervision) est la seule exception documentée.

## 6. Matrice — finances

| Action | PLATFORM_ADMIN | ORG_OWNER | PROJECT_OWNER | ENGINEER | CONTRACTOR | FIELD_AGENT | VALIDATOR | FINANCE | INVESTOR |
|---|---|---|---|---|---|---|---|---|---|
| Voir budget et solde | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| Créer une dépense | ✅ | ✅ | ✅ | ❌ | ✅ (si `can_manage_finance`) | ❌ | ❌ | ✅ | ❌ |
| Approuver / rejeter une dépense | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Enregistrer un paiement | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Modifier le budget | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Voir les journaux financiers | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |

**Trois niveaux, implémentés dans `apps/finance/access.py` (phase 7)** :

1. `view_finance` — lire le budget, les dépenses, les paiements et le grand livre ;
2. `manage_finance` — créer, corriger, soumettre une dépense et déposer un justificatif
   (le drapeau `can_manage_finance` suffit — un contractant peut saisir ses factures) ;
3. **engagement** (`can_settle_finance`) — approuver, rejeter, annuler, payer, tenir le budget,
   ajuster : capacité `manage_finance` **et** rôle ∈ {PLATFORM_ADMIN, ORG_OWNER, PROJECT_OWNER,
   FINANCE}. Un contractant doté du drapeau n'engage donc jamais d'argent (décision ADR-012).

Un projet hors périmètre répond **404** (son existence n'est pas révélée) ; un membre sans
capacité financière répond **403**. Détail des règles : `docs/flows/finance.md` §9.

## 7. Matrice — journalisation et exploitation

| Action | PLATFORM_ADMIN | ORG_OWNER | PROJECT_OWNER | ENGINEER | CONTRACTOR | FIELD_AGENT | VALIDATOR | FINANCE | INVESTOR |
|---|---|---|---|---|---|---|---|---|---|
| Voir l'activité du projet | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Voir l'activité de l'organisation | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Voir les journaux d'authentification | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Voir les métriques / tâches Celery | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

## 8. Implémentation

- Permissions DRF composées : `IsAuthenticated` + `HasProjectAccess` + prédicats métier
  (`CanValidateEvidence`, `CanManageFinance`, `IsProjectOwnerOrAbove`).
- Filtrage systématique des querysets par appartenance : un projet non autorisé n'existe pas
  (réponse `404` sur le détail, absent des listes) — le `403` est réservé aux cas où l'objet est
  visible mais l'action interdite, conformément aux critères d'acceptation de MVP-005.
- Constantes centralisées dans `apps/users/roles.py` : une seule source de vérité, partagée avec
  le frontend via `GET /api/meta/roles/`.
- Capacités de planification (Phase 4) : `MANAGE_SCHEDULE` (créer/planifier jalons et tâches)
  et `UPDATE_TASK` (mettre à jour l'exécution d'une tâche dont on est responsable). Un
  `CONTRACTOR` porte `UPDATE_TASK` sans `MANAGE_SCHEDULE` : il exécute, il ne replanifie pas.
- Résolution des capacités projets dans `apps/projects/access.py` : `resolve_capabilities()` pour
  un projet, `build_capabilities_map()` pour une collection (une passe, pas de N+1), et
  `permissions_payload()` pour le champ `permissions` de l'API.
- **Chaque permission critique a un test positif et un test négatif** (exigence MVP-004) :
  `tests/permissions/test_matrix.py` génère la matrice sous forme de tests paramétrés
  (rôle × action × attendu), ce qui rend toute divergence doc/code visible immédiatement.
