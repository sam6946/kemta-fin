# Flux organisation → projet → membres (Phase 3, MVP-005)

Statut : implémenté et testé. Références : `docs/rbac-matrix.md`, `docs/data-model.md`,
`docs/api-contract.md` §4.

## 1. Pourquoi une organisation au-dessus du projet

Un projet de chantier n'existe jamais seul : il a un maître d'ouvrage, un cadre juridique et
une équipe. L'organisation porte ce cadre ; le projet porte l'exécution. Règles :

- un projet est **toujours** rattaché à une organisation (FK obligatoire, non nullable) ;
- l'organisation regroupe des membres qui voient ses projets selon leur rôle ;
- l'accès à un projet s'obtient par **appartenance au projet** (`ProjectMember`) ou par
  **périmètre d'organisation** (propriétaire d'organisation, membre `ORG_OWNER`).

## 2. Parcours de création

```
1. POST /api/auth/register/ + OTP                → compte activé (téléphone)
2. POST /api/organizations/                      → l'auteur devient propriétaire (ORG_OWNER)
   └─ journalisé : ORG_CREATED
3. POST /api/projects/ {organization, name, budget_total, status, dates…}
   └─ l'auteur devient membre PROJECT_OWNER du projet (validation preuves + finances)
   └─ journalisé : PROJECT_CREATED (nom, code, devise, budget, statut)
4. POST /api/projects/{id}/members/ {phone, role, flags}
   └─ journalisé : MEMBER_ADDED
5. PATCH /api/projects/{id}/members/{member_id}/ {role, flags}
   └─ journalisé : MEMBER_ROLE_CHANGED (ancien/nouveau rôle, anciennes/nouvelles capacités)
6. DELETE /api/projects/{id}/members/{member_id}/ → retrait logique (is_active=False)
   └─ journalisé : MEMBER_REMOVED
```

## 3. Règles d'accès (autorité : backend)

| Situation | Réponse |
|---|---|
| Projet hors périmètre (non membre, organisation non pilotée) | **404** — l'objet « n'existe pas » pour l'utilisateur |
| Projet visible mais action interdite au rôle | **403** `permission_denied` |
| Ajout d'un membre par un non-responsable | **403** |
| Projet d'une organisation dont on n'est pas responsable (création) | **400** (champ `organization`) |
| Dernier responsable du projet rétrogradé ou retiré | **409** `last_manager` |
| Créateur du projet retiré | **409** `cannot_remove_creator` |
| Organisation contenant des projets actifs supprimée | **409** `organization_has_projects` |

Le rôle **par projet** prime sur le rôle global : un utilisateur `INVESTOR` global peut être
`ENGINEER` sur un projet et n'agir qu'en conséquence sur ce projet.

Les capacités exposées au frontend (`permissions` dans la réponse projet) sont calculées par le
backend ; l'interface masque les actions non autorisées mais ne décide jamais.

## 4. Ajout d'un membre : compte existant obligatoire

Le MVP ajoute des **comptes existants** identifiés par leur numéro de téléphone :
`POST /api/projects/{id}/members/ { "phone": "+2376XXXXXXXX", "role": "ENGINEER" }`.

- numéro inconnu → **404** `user_not_found` (« l'utilisateur doit d'abord s'inscrire ») ;
- compte non activé → **409** `user_not_activated` ;
- déjà membre → **409** `member_already_exists`.

**Décision assumée** : l'invitation d'un numéro inconnu par SMS (lien d'inscription pré-rempli)
est hors périmètre MVP. Raison : elle suppose un jeton d'invitation, une page d'acceptation et
une politique anti-abus supplémentaires, pour un gain faible sur les premières versions où les
équipes se connaissent déjà. À l'inverse, accepter un numéro inconnu sans contrôle créerait des
comptes fantômes. Évolution prévue : `ProjectInvitation` (jeton, expiration, SMS), en P2.

## 5. Capacités affichées sur un projet

`GET /api/projects/{id}/` renvoie :

```json
"permissions": {
  "edit_project": true, "archive_project": true, "manage_members": true,
  "capture_evidence": true, "validate_evidence": false,
  "view_finance": true, "manage_finance": false
}
```

Ces valeurs proviennent de la matrice `apps/users/roles.py` + des flags de membre
(`can_validate_evidence`, `can_manage_finance`).

## 6. Performance

- Les listes de projets et d'organisations annotent leurs compteurs (`member_count`,
  `project_count`) et chargent les relations utiles en une requête (`select_related`).
- Les permissions de toute une page de projets sont résolues **en une passe**
  (`build_capabilities_map`) : sans cette vectorisation, un N+1 a été détecté par les tests
  (162 requêtes pour 10 projets) puis supprimé. Le seuil est verrouillé par
  `test_project_list_has_no_n_plus_one`.

## 7. Étapes suivantes

- Phase 4 : jalons et tâches rattachés au projet, avec calcul d'avancement serveur
  (`Project.progress` est déjà en lecture seule).
- Phase 9 : écran d'activité du projet (journal déjà alimenté avec `project`/`organization`).
