# État d'avancement — MVP KEMTA SUIVI

Dernière mise à jour : phases 0, 1, 2, 3, 4, 5, 6 et 7 livrées.
Preuves d'exécution : `cd backend && pytest --cov=apps` → **468 tests** (2 ignorés : concurrence
PostgreSQL), couverture **96 %** ; `cd frontend && npm test` → **102 tests** ; `npm run build` →
OK ; `ruff check` + `ruff format --check` → propres ; `npm run lint` → propre.
Parcours vérifiés en direct derrière le proxy frontend : upload multipart, rejeu idempotent
(preuve et lot), galerie, décision de validation, téléchargement de la miniature, lot de
synchronisation (application, conflits classés, rejeu sans second effet), parcours financier
complet (budget → dépense → approbation → paiement → contre-écriture).

## Vue par phase

| Phase | Contenu | État |
|---|---|---|
| 0 — Cadrage | backlog, architecture + ADR, modèle de données, matrice RBAC, contrat API, offline/sync, plan de tests, flux auth et projet | ✅ livrée |
| 1 — Fondations | Docker Compose, PostgreSQL/Redis/Celery, `/api/health/`, logs JSON, `.env.example`, migrations, tests, README | ✅ livrée |
| 2 — Authentification et RBAC | téléphone + OTP, connexion JWT, refresh, RBAC 9 rôles, **réinitialisation du mot de passe**, rate limiting, journalisation | ✅ livrée |
| 3 — Organisations, projets, membres | organisations, projets, membres, rôles par projet, permissions backend, écrans responsive, tests de permissions | ✅ livrée |
| 4 — Jalons, tâches, planning | jalons, tâches, planning listé, avancement serveur, alertes de retard | ✅ livrée |
| 5 — Preuves terrain | capture, compression, GPS, hash, statuts, validations | ✅ livrée |
| 6 — Offline-first | IndexedDB, file de synchronisation, idempotence, conflits | ✅ livrée |
| 7 — Budget, dépenses | budget, postes, dépenses, paiements, transactions atomiques | ✅ livrée |
| 8 — Dashboard agrégé | endpoint `/api/projects/{id}/dashboard/`, alertes, cache | ⏳ à venir |
| 9 — Journalisation étendue | suppression logique, écran d'activité, journaux protégés | 🟡 partielle (modèle + événements phases 2→5) |
| 10 — Asynchrone et notifications | Celery, événements métier, notifications in-app | 🟡 partielle (Celery + SMS/email async) |
| 11 — Performance et observabilité | pagination, N+1, cache, métriques, tests de charge | 🟡 partielle (pagination, N+1 verrouillés par tests) |

## Vue par fonctionnalité (P0)

| ID | Fonctionnalité | État | Preuves |
|---|---|---|---|
| MVP-001 | Inscription par téléphone | ✅ | `apps/users/tests/test_registration.py` |
| MVP-002 | OTP SMS sécurisé | ✅ | `test_otp.py` (hash, expiration, tentatives, renvois, `purpose`) |
| MVP-003 | Connexion et sessions | ✅ | `test_login.py` (rotation, révocation, verrouillage) |
| MVP-004 | RBAC et permissions backend | ✅ | `test_role_matrix.py`, `apps/projects/tests/test_access_matrix.py` |
| MVP-005 | Organisations, projets, membres | ✅ | `test_organizations.py`, `test_projects.py`, `test_members.py` |
| MVP-006 | Jalons, tâches, avancement | ✅ | `test_milestones.py` (14) · `test_tasks.py` (17) · `test_progress.py` (18) |
| MVP-007 | Capture de preuve terrain | ✅ | `apps/evidences/tests/test_capture.py` (20) · `src/lib/__tests__/media.test.ts` (13) · `src/pages/__tests__/ProjectEvidences.test.tsx` (13) |
| MVP-008 | Validation et historique des preuves | ✅ | `apps/evidences/tests/test_validation.py` (20) |
| MVP-009 | File offline et synchronisation | ✅ | `apps/sync/tests/test_batch.py` (26) · `src/lib/__tests__/outbox.test.ts` (17) · `src/sync/__tests__/SyncProvider.test.tsx` (4) · `src/pages/__tests__/SyncPage.test.tsx` (6) · capture hors ligne dans `ProjectEvidences.test.tsx` (3) |
| MVP-010 | Budget et dépenses | ✅ | `apps/finance/tests/` : `test_budget.py` (10) · `test_expenses.py` (19) · `test_payments.py` (9) · `test_ledger.py` (15) · `test_permissions.py` (16) · `test_rollback.py` (9) · `test_concurrency.py` (5, dont 2 PostgreSQL) · `test_guards.py` (11) · `test_seed_finance.py` (8) · `src/pages/__tests__/ProjectFinance.test.tsx` (8) |
| MVP-011 | Dashboard projet agrégé | ⏳ phase 8 | compteurs réels déjà affichés (pas de mock) |
| MVP-012 | Journal d'activité | 🟡 | modèle immuable + événements auth/org/projet/membres ; écran d'activité en phase 9 |
| MVP-013 | Médias | 🟡 | compression côté appareil (≤ 1600 px, q0.82) + miniature WebP 320 px et version liste JPEG 1080 px générées par Celery ; antivirus/quotas en phase 10 |
| MVP-014 | Notifications et événements | ⏳ phase 10 | SMS/email déjà traités par Celery |
| MVP-015 | Observabilité et healthchecks | 🟡 | `/api/health/`, logs JSON, `request_id`, métriques à compléter en phase 11 |
| MVP-016 | Tests E2E et seed | 🟡 | seed dev complet (9 comptes, 3 organisations, 4 projets FCFA, membres) ; E2E Playwright en phase 11 |
| MVP-017 | **Réinitialisation du mot de passe** | ✅ | `test_password_reset.py` (20 cas), `test_security.py` |
| MVP-018 | Réinitialisation par email (P1/P2) | ⏳ | email vérifié par OTP déjà disponible |

## Événements journalisés (immuables)

`USER_REGISTERED`, `OTP_SENT/VERIFIED/FAILED/RESEND`, `LOGIN_SUCCESS/FAILED`, `ACCOUNT_LOCKED`,
`LOGOUT`, `PASSWORD_RESET_REQUESTED/FAILED/CONFIRMED/DENIED`, `PASSWORD_CHANGED`,
`EMAIL_ADDED/VERIFIED`, `ORG_CREATED/UPDATED`, `PROJECT_CREATED/UPDATED/ARCHIVED`,
`MEMBER_ADDED/ROLE_CHANGED/REMOVED`, `MILESTONE_CREATED/UPDATED/DELETED`,
`TASK_CREATED/UPDATED/STATUS_CHANGED/DELETED`, `EVIDENCE_CAPTURED/VALIDATED/REJECTED/FLAGGED/REOPENED`,
`BUDGET_LINE_CREATED/UPDATED/DELETED`, `EXPENSE_CREATED/UPDATED/SUBMITTED/APPROVED/REJECTED/CANCELLED`,
`EXPENSE_RECEIPT_ATTACHED`, `PAYMENT_RECORDED/CANCELLED`, `ADJUSTMENT_RECORDED`,
`BUDGET_THRESHOLD_REACHED`, `BUDGET_EXCEEDED`.

## File hors ligne (phase 6)

| Élément | Où | Rôle |
|---|---|---|
| File locale IndexedDB | `frontend/src/lib/db.ts`, `outbox.ts` | opérations persistées (binaire de la photo en `ArrayBuffer`), retry exponentiel borné à 8 essais, statuts `PENDING`/`UPLOADING`/`SYNCED`/`FAILED`/`CONFLICT` |
| Moteur de reprise | `frontend/src/sync/SyncProvider.tsx` | reprise au démarrage, sur `online`, au retour d'onglet et après mise en file ; un seul réveil programmé par échéance (aucun polling) |
| Lot serveur idempotent | `apps/sync/` (`POST /api/sync/batch/`) | registre `SyncOperation` (clé unique par utilisateur), application isolée par opération, rejeu de la réponse d'origine, `CONFLICT`/`FAILED` explicites |
| Écran de suivi | `/synchronisation` + `SyncBadge` | ce qui reste à envoyer, motifs d'échec, relance unitaire ou globale, abandon |

## Écritures financières (phase 7)

| Élément | Où | Rôle |
|---|---|---|
| Grand livre *append-only* | `apps/finance/models.py` (`FinancialTransaction`) | chaque écriture porte `balance_after` ; modification/suppression impossibles (modèle, queryset, admin) |
| Services transactionnels | `apps/finance/services.py` | `transaction.atomic()` + `select_for_update()` sur le projet, seuils, contre-écritures, justificatifs |
| Permissions | `apps/finance/access.py` | `view_finance` / `manage_finance` / **engagement** (rôles de pilotage uniquement) |
| API | `apps/finance/views.py` + `urls.py` | 11 routes (17 opérations F1→F17), projet hors périmètre → 404, listes sans N+1 |
| Écran | `frontend/src/pages/ProjectFinance.tsx` | budget, dépenses, paiements, grand livre, alertes — aucun montant recalculé côté client |

## Points ouverts (ADR)

| # | Décision | Échéance |
|---|---|---|
| ADR-004 | Upload direct Django vs presigned URL S3 | ✅ tranchée en phase 5 : upload direct (multipart) avec `Idempotency-Key` ; presigned S3 réévalué si le volume l'exige |
| ADR-005 | Fournisseur SMS (coût par OTP, couverture réseau) | ⏳ avant mise en production (adaptateur console en dev) |
| ADR-006 | Stockage média : volume chiffré vs S3-compatible | ✅ tranchée en phase 5 : volume local privé, accès servi par l'API, `X-Accel-Redirect` en production ; S3-compatible possible sans changer le contrat (chemins relatifs) |
| ADR-007 | Procédure « numéro perdu / changement de SIM » | avant mise en production |
| ADR-008 | Invitations par SMS (`ProjectInvitation`) | phase ultérieure |
| ADR-009 | Gantt graphique (barres temporelles) vs planning listé | retour utilisateur avant phase 8 |
| ADR-010 | Calendrier ouvré pour le calcul des retards (jours fériés camerounais) | avant mise en production |
| ADR-011 | Dépassement de budget : blocage strict ou validation motivée | ✅ tranchée en phase 7 : refus `422` par défaut, dépassement possible avec un **motif écrit** (≥ 10 caractères), journalisé et toujours alerté |
| ADR-012 | Périmètre du drapeau `can_manage_finance` | ✅ tranchée en phase 7 : gestion (créer/corriger/soumettre) sans droit d'engagement — approuver, payer et tenir le budget restent réservés aux rôles de pilotage |
