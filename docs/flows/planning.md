# Flux planification : jalons, tâches et avancement (Phase 4, MVP-006)

Statut : implémenté et testé. Références : `docs/data-model.md` §3, `docs/api-contract.md`,
`docs/rbac-matrix.md`.

## 1. Objets et vocabulaire

| Objet | Rôle | Champs structurants |
|---|---|---|
| **Jalon** (`Milestone`) | étape datée du chantier, porte la pondération de l'avancement | `title`, `status`, `planned_date`, `actual_date`, `order`, `weight` |
| **Tâche** (`Task`) | unité de travail exécutable, rattachable à un jalon | `title`, `status`, `planned_start_date`, `planned_end_date`, `actual_start_date`, `actual_end_date`, `progress`, `weight`, `assignee`, `depends_on` |

Statuts des jalons : `PLANNED`, `IN_PROGRESS`, `DONE`, `BLOCKED`, `CANCELLED`.
Statuts des tâches : `TODO`, `IN_PROGRESS`, `DONE`, `BLOCKED`, `CANCELLED`.
Ces listes sont validées côté backend (aucun statut libre) et exposées par
`GET /api/meta/status/`.

## 2. Règles de validation (backend, jamais négociables côté client)

1. `planned_start_date <= planned_end_date` et `actual_start_date <= actual_end_date`,
   sinon `400` avec le champ fautif (`planned_end_date`, `actual_end_date`).
2. `actual_date` (jalon) / `actual_end_date` (tâche) : autorisées **seulement** si le statut
   est terminal (`DONE` ou `CANCELLED`), et obligatoires pour `DONE`.
3. Une tâche `TODO` ne peut pas porter de date de début réelle.
4. Une tâche `DONE` est forcée à 100 % d'avancement ; une tâche `CANCELLED` ne compte pas
   dans les ratios.
5. `progress` : pourcentage borné 0-100, arrondi à deux décimales.
6. `weight` : strictement positif (poids neutre = 1).
7. Un jalon ou une dépendance appartient **au même projet** que la tâche.
8. Les dépendances entre tâches forment un graphe **acyclique** : dépendre de soi-même ou
   refermer une boucle donne `409 dependency_cycle`.
9. `Project.progress` est **en lecture seule** dans l'API.

## 3. Calcul de l'avancement (source unique : `apps/projects/progress.py`)

```
avancement_tâche   = 100 si DONE, 0 si CANCELLED, sinon progress
avancement_jalon   = moyenne pondérée (weight) des tâches actives du jalon
                     si le jalon n'a aucune tâche active : 100 si DONE, sinon 0
avancement_projet  = moyenne pondérée (weight) des jalons non annulés
                     + un groupe « tâches sans jalon » (poids = somme de leurs poids)
projet sans jalon ni tâche = 0 %
```

Exemple (données de test `test_progress.py`) : jalon terminé de poids 2, second jalon de
poids 3 contenant une tâche terminée (poids 1) et une tâche à 40 % (poids 3)
→ jalon 2 = (100 × 1 + 40 × 3) / 4 = 55 ; projet = (100 × 2 + 55 × 3) / 5 = **73 %**.

Le calcul est déclenché **après chaque écriture** (création, modification, suppression de
jalon ou de tâche) et persisté dans `Project.progress`. Le endpoint de lecture
(`/schedule/`, `/delays/`) recalcule à la volée sans écrire, ce qui évite tout affichage
périmé si la donnée a été modifiée hors API (import, seed, script).

## 4. Retards (déterministes)

- tâche en retard : `planned_end_date < aujourd'hui` **et** statut non terminal ;
- jalon en retard : `planned_date < aujourd'hui` **et** statut non terminal ;
- `days_late` = nombre de jours entiers de dépassement ;
- `GET /api/projects/{id}/delays/` liste ces éléments avec leur motif et la date de référence
  (le calcul ne dépend pas de l'heure : aucune dérive de fuseau).

## 5. Permissions

| Action | Capacité requise |
|---|---|
| Lire le planning (`/schedule/`, `/delays/`, listes, détails) | accès au projet (membre ou organisation pilotée) |
| Créer/modifier/supprimer un jalon | `MANAGE_SCHEDULE` |
| Créer/supprimer une tâche | `MANAGE_SCHEDULE` |
| Modifier une tâche (statut, avancement, dates réelles, description) | `MANAGE_SCHEDULE`, ou `UPDATE_TASK` **et** être le responsable désigné (ou aucun responsable) |
| Modifier la planification d'une tâche (dates prévues, jalon, poids, dépendances, titre) | `MANAGE_SCHEDULE` |

Projet hors périmètre → `404` ; droit manquant sur un projet visible → `403`.

## 6. Journalisation

`MILESTONE_CREATED`, `MILESTONE_UPDATED` (`changed` = ancien/nouveau statut et date),
`MILESTONE_DELETED`, `TASK_CREATED`, `TASK_UPDATED`, `TASK_STATUS_CHANGED`,
`TASK_DELETED`. Chaque événement porte le projet, l'organisation, l'auteur et
`project_progress` après opération.

## 7. Performance

- `/schedule/` charge jalons + tâches + responsables + dépendances en un nombre constant de
  requêtes : le seuil est verrouillé par `test_schedule_has_no_n_plus_one` (≤ 12 requêtes
  pour 11 jalons et 30 tâches) ;
- l'avancement est calculé à partir des collections déjà chargées (`compute_project_progress`
  accepte `milestones=` et `tasks=`), ce qui évite une requête par jalon.

## 8. Interface (mobile d'abord)

- `/projets/{id}` affiche le planning : avancement calculé, jalons ordonnés, tâches
  rattachées, badges « en retard », alertes ;
- les actions d'écriture ne sont proposées que si `permissions.manage_schedule` (ou
  `permissions.update_task` pour le responsable) est vrai ;
- la mise à jour de l'avancement d'une tâche est possible en un geste (curseur + bouton).
