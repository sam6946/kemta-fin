# kemta_suivi

Suivi de chantier (Cameroun) : preuves terrain **offline-first**, jalons et planning, budget en
**FCFA**, journal d'activité. Backend Django/DRF + PostgreSQL/Redis/Celery, frontend
React/TypeScript/Vite.

**Avancement actuel : phases 0 (cadrage), 1 (fondations), 2 (authentification, RBAC et
réinitialisation du mot de passe), 3 (organisations, projets, membres), 4 (jalons, tâches,
planning), 5 (preuves terrain : capture, validation, historique) et 6 (offline-first :
file locale, synchronisation automatique, conflits) livrées et testées.**
Suite : finances (phase 7), dashboard agrégé (phase 8).
Détail : [`docs/STATUS.md`](docs/STATUS.md).

## 1. Démarrage rapide avec Docker (chemin nominal)

```bash
cp .env.example .env          # puis remplacez SECRET_KEY
docker compose up -d --build  # db + redis + web + worker + beat + frontend
docker compose exec web python manage.py seed_dev   # comptes + organisations + projets (dev)
```

- Frontend : <http://localhost:5173>
- API : <http://localhost:8000/api/>
- Santé : <http://localhost:8000/api/health/> (vérifie application, PostgreSQL et Redis)
- Admin : <http://localhost:8000/admin/>

Comptes de démonstration (un par rôle) — création par `seed_dev`, **développement uniquement** :

| Rôle | Téléphone | Mot de passe |
|---|---|---|
| Project owner | `+237 690 000 003` | `Kemta#2026Demo` |
| Ingénieur | `+237 690 000 004` | `Kemta#2026Demo` |
| Agent terrain | `+237 690 000 006` | `Kemta#2026Demo` |
| Financier | `+237 690 000 008` | `Kemta#2026Demo` |

`seed_dev` crée aussi 3 organisations (Douala, Kribi, Yaoundé) et 4 projets réalistes en FCFA
(22,5 à 320 millions), avec leurs membres — de quoi naviguer immédiatement dans `/projets`.

**Tester le parcours OTP sans téléphone** : en développement, le fournisseur SMS est un
adaptateur console et l'écran d'activation comme l'écran de réinitialisation affichent un bouton
« Afficher les codes de test » qui lit `GET /api/dev/outbox/` (endpoint désactivé — HTTP 404 —
dès que `DEBUG=false` ou `DJANGO_ENV=production`). Aucun code OTP n'est écrit dans les logs.

## 2. Démarrage sans Docker

```bash
# Backend (SQLite en local, aucun service externe requis)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
USE_SQLITE=true USE_LOCAL_CACHE=true CELERY_TASK_ALWAYS_EAGER=true \
  python manage.py migrate && python manage.py seed_dev
USE_SQLITE=true USE_LOCAL_CACHE=true CELERY_TASK_ALWAYS_EAGER=true \
  python manage.py runserver 0.0.0.0:8000

# Frontend (proxy /api → http://127.0.0.1:8000)
cd frontend && npm install && npm run dev
```

## 3. Tests

```bash
cd backend && pytest                       # 368 tests, sans infrastructure externe
cd backend && pytest --cov=apps            # couverture (95 %)
cd backend && ruff check . && ruff format --check .    # lint + formatage
cd frontend && npm run lint && npm test   # lint ESLint + 94 tests : mot de passe oublié, projets, membres,
                                          # planning, preuves terrain (compression, GPS, envoi), file hors ligne
                                          # (persistance, retry, conflits, reprise automatique), formatage FCFA
cd frontend && npm run build               # vérification TypeScript + build
```

Les tests backend tournent sur SQLite, cache mémoire, Celery en mode eager et SMS/email en
adaptateur console : **aucun service externe n'est nécessaire**.

## 4. Périmètre d'authentification livré (Phase 2)

- Identifiant principal : **numéro de téléphone** normalisé E.164 (Cameroun), l'email est facultatif.
- Inscription → **OTP SMS haché, expirable, à usage unique**, tentatives et renvois limités,
  compte inactif jusqu'à validation.
- Connexion téléphone + mot de passe, JWT (access 15 min, refresh rotatif + blacklist),
  verrouillage temporaire après échecs répétés.
- **Mot de passe oublié (MVP-017)** : `/mot-de-passe-oublie` → OTP SMS → nouveau mot de passe →
  **révocation de toutes les sessions**, SMS de confirmation, journalisation, réponse neutre
  (aucune énumération de compte).
- Changement de mot de passe pour un utilisateur connecté (ancien mot de passe requis).
- 9 rôles et matrice de capacités testée en positif **et** en négatif.
- Journal d'activité **immuable** (auth, OTP, rôles, mots de passe) — suppression interdite.
- Rate limiting par numéro/IP, enveloppe d'erreur uniforme avec `request_id`, logs structurés
  sans secret.

## 5. Périmètre organisations / projets / membres (Phase 3)

- **Organisation** : racine du périmètre (propriétaire + membres), un projet y est toujours rattaché.
- **Projet** : nom, code unique par organisation, ville/région, coordonnées, rayon de périmètre,
  devise **XAF**, budget en FCFA entiers, statut, dates prévues/réelles, avancement **calculé
  côté serveur** (lecture seule).
- **Membres** : rôle par projet + capacités fines (`can_validate_evidence`, `can_manage_finance`) ;
  ajout d'un **compte existant** via son numéro de téléphone (invitation SMS hors périmètre MVP).
- **Permissions** : le backend filtre les querysets (projet hors périmètre → 404) et renvoie un
  champ `permissions` que le frontend utilise pour masquer les actions (objet visible mais action
  interdite → 403).
- **Journalisation** : `ORG_CREATED/UPDATED`, `PROJECT_CREATED/UPDATED/ARCHIVED`,
  `MEMBER_ADDED/ROLE_CHANGED/REMOVED` (avec ancienne/nouvelle valeur).
- Écrans : `/organisations` (liste + création) et `/projets` (liste, filtres, création),
  `/projets/:id` (détail + gestion des membres).

## 6. Périmètre planification (Phase 4)

- **Jalons** : étape datée avec statut, date prévue/réelle, ordre et **poids** (pondération de
  l'avancement) ; suppression logique.
- **Tâches** : rattachables à un jalon, avec dates prévues/réelles, avancement, poids,
  responsable désigné et **dépendances sans cycle** (une boucle est refusée en 409).
- **Avancement calculé côté serveur** : moyenne pondérée des jalons (et des tâches sans jalon),
  recalculée à chaque écriture ; `progress` est en lecture seule dans l'API.
- **Retards déterministes** : une tâche ou un jalon non terminal dont la date prévue est
  dépassée, avec le nombre de jours — exposés par `/api/projects/{id}/delays/` et un résumé
  d'alertes dans `/api/projects/{id}/schedule/`.
- **Permissions** : `manage_schedule` pour planifier, `update_task` pour qu'un responsable
  fasse avancer sa tâche (le rôle CONTRACTOR exécute sans replanifier).
- Écran : `/projets/{id}` — planning ordonné, jalons, tâches, alertes et avancements.

## 7. Périmètre preuves terrain (Phase 5)

- **Capture** : photo compressée **sur l'appareil** (≤ 1600 px de côté, qualité 0,82) et empreinte
  **SHA-256** calculée avant l'envoi ; envoi `multipart` avec `Idempotency-Key` obligatoire — un
  envoi réessayé après une coupure réseau n'est jamais dupliqué (`200` + `Idempotency-Replayed:
  true`), et une photo déjà déposée est détectée par son hash (`409 duplicate_evidence`).
- **Authenticité** : le contenu réel du fichier est vérifié (magic bytes : JPEG/PNG/WebP, ≤ 10 Mo,
  ≤ 4000 px), le nom d'origine est ignoré et le chemin de stockage est régénéré côté serveur.
- **GPS explicite** : la position est demandée à l'utilisateur (`AVAILABLE` / `UNAVAILABLE` /
  `DENIED`), la distance au chantier est calculée (Haversine) et un envoi hors périmètre est
  refusé en `422` quand `EVIDENCE_GEOFENCE_ENFORCE` est actif — jamais d'échec silencieux.
- **Validation** : machine à états fermée (`PENDING` / `VALIDATED` / `REJECTED` / `FLAGGED`),
  commentaire obligatoire pour un rejet ou un signalement, personne ne valide sa propre preuve,
  historique append-only (acteur, date, action, commentaire) et `ActivityLog` à chaque décision.
- **Médias** : miniature WebP 320 px et version allégée JPEG 1080 px générées par Celery ; les
  fichiers ne sont jamais publics — l'accès passe par l'API (`/api/evidences/{id}/file/`), avec
  `Cache-Control: private` et délégation possible à Nginx (`MEDIA_X_ACCEL_REDIRECT`).
- Écran : `/projets/{id}` → section **Preuves** (capture guidée, galerie avec statuts, détail,
  validation et historique).

## 8. Périmètre offline-first (Phase 6)

- **Le terrain n'attend jamais le réseau** : une photo capturée hors ligne est compressée,
  empreintée puis **écrite dans IndexedDB** (binaire compris, en `ArrayBuffer`) avec sa clé
  d'idempotence — elle survit à la fermeture de l'application.
- **Synchronisation automatique** : reprise au démarrage, à l'événement `online`, au retour de
  l'onglet et après chaque mise en file. Aucune action obligatoire ; un seul réveil programmé par
  échéance, donc **aucun polling**.
- **Idempotence de bout en bout** : la même clé est réutilisée à chaque tentative. Côté serveur,
  `POST /api/evidences/` (fichier) et `POST /api/sync/batch/` (opérations sans fichier) renvoient
  le résultat d'origine au lieu de réappliquer l'opération ; un doublon de photo est détecté par
  son empreinte SHA-256.
- **Reprise maîtrisée** : délai exponentiel `min(30 s, 1 s × 2^n)` avec gigue, plafonné à 8 essais,
  puis relance manuelle — jamais de boucle infinie silencieuse.
- **Conflits visibles** : permission, transition impossible, hors périmètre, élément supprimé…
  sont classés `CONFLICT`, présentés avec leur motif, avec « Relancer » ou « Abandonner ».
- **Suivi** : badge global (hors ligne / en attente / à vérifier) et écran `/synchronisation`
  (compteurs, motif d'échec, essais, relance unitaire ou globale). Les preuves encore locales
  apparaissent dans la galerie du chantier, distinctes des preuves confirmées par le serveur.
- **Actions en ligne uniquement** (jamais mises en file en silence) : inscription, connexion, OTP
  et **réinitialisation du mot de passe** — l'interface l'annonce et propose « Réessayer ».

## 9. Périmètre financier (Phase 7)

- **Budget tenu côté serveur** : postes budgétaires (somme ≤ budget du projet), dépenses,
  paiements et un **grand livre *append-only*** qui porte le solde après chaque écriture.
- **Montants en FCFA entiers** : les centimes sont refusés, jamais arrondis en silence. Aucun
  total n'est accepté en entrée : le consommé, le payé, le solde et le taux sont **calculés en SQL**
  depuis le grand livre, et les listes restent à nombre de requêtes constant (pas de N+1).
- **Atomique et concurrent** : chaque opération critique est enveloppée dans
  `transaction.atomic()` avec `select_for_update()` sur le projet ; deux paiements simultanés ne
  peuvent pas dépasser le montant dû, et une panne en cours d'écriture ne laisse rien derrière
  (tests de rollback et de concurrence dédiés).
- **Dépassements visibles** : l'engagement au-delà du budget (ou d'un poste) est refusé
  (`422`) sauf **motif explicite** (≥ 10 caractères), alors journalisé ; les franchissements des
  seuils 80 % et 100 % sont journalisés une seule fois, et les alertes restent affichées.
- **Permissions à trois niveaux** : consulter (`view_finance`), gérer une dépense avant
  approbation (`manage_finance`), **engager de l'argent** (`settle_finance` — rôles de pilotage
  uniquement). Un contractant peut saisir ses factures, jamais les approuver ni les payer.
- **Traçabilité** : chaque création, modification, approbation, rejet, annulation, paiement et
  ajustement est journalisé avec auteur, date et **valeurs avant/après** ; une correction passe
  par une contre-écriture, jamais par une suppression.
- **Justificatifs** : PDF ou photo (JPEG/PNG/WebP) vérifiés par leur **signature binaire**,
  empreints en SHA-256, servis de façon privée.
- **En ligne uniquement** : les opérations financières ne sont pas mises en file hors ligne —
  l'interface l'indique et propose de réessayer (contrairement aux preuves terrain).

## 10. Documentation

| Document | Contenu |
|---|---|
| [`docs/BACKLOG_MVP.md`](docs/BACKLOG_MVP.md) | Backlog priorisé, phases 0 → 11, MVP-001 → MVP-018, définition de terminé commune |
| [`docs/architecture.md`](docs/architecture.md) | Architecture, Docker, stockage médias, observabilité, sécurité, ADR |
| [`docs/data-model.md`](docs/data-model.md) | Modèle de données cible (identité → finances → exploitation) |
| [`docs/rbac-matrix.md`](docs/rbac-matrix.md) | 9 rôles, capacités, matrices détaillées, implémentation |
| [`docs/api-contract.md`](docs/api-contract.md) | Contrat API : endpoints, erreurs, idempotence, rate limiting |
| [`docs/test-plan.md`](docs/test-plan.md) | Plan de tests, matrice fonctionnalité → tests, seed, E2E |
| [`docs/flows/authentication.md`](docs/flows/authentication.md) | Flux inscription / OTP / connexion / **réinitialisation du mot de passe** |
| [`docs/flows/project.md`](docs/flows/project.md) | Flux organisation → projet → membres, règles d'accès 403/404, performance |
| [`docs/flows/planning.md`](docs/flows/planning.md) | Flux jalons/tâches, règles de calcul d'avancement et de retard, permissions |
| [`docs/flows/evidences.md`](docs/flows/evidences.md) | Flux preuve terrain : capture hors ligne, envoi idempotent, périmètre, validation, historique |
| [`docs/flows/finance.md`](docs/flows/finance.md) | Flux financier : budget, cycle de vie des dépenses, paiements, dépassements, contre-écritures, concurrence |
| [`docs/offline-sync.md`](docs/offline-sync.md) | Stratégie offline-first : file locale, reprise, conflits, cache (implémentée en phase 6) |
| [`docs/STATUS.md`](docs/STATUS.md) | État d'avancement phase par phase et fonctionnalité par fonctionnalité |

## 11. Structure du dépôt

```
backend/     config/ (settings, urls, celery) · apps/core (journal, santé, erreurs, montants) ·
             apps/users (identité, OTP, sessions, rôles) ·
             apps/organizations (organisations, membres) ·
             apps/projects (projets, membres, règles d'accès) · apps/evidences (preuves, validations) ·
             apps/sync (lot de synchronisation idempotent) · tests
frontend/    src/ (api, auth, components, pages) · tests unitaires (vitest)
docs/        cadrage Phase 0 et spécifications
docker-compose.yml · docker-compose.prod.yml · .env.example
```

## 12. Règles non négociables du projet

1. Aucun secret dans le dépôt : uniquement des variables d'environnement (`.env.example` documenté).
2. Aucun OTP, mot de passe ou jeton en clair — ni en base, ni dans les logs, ni dans les réponses.
3. Le backend est l'autorité : le frontend masque, il ne décide pas.
4. Aucune donnée mockée ni bouton fictif dans le parcours produit.
5. Les montants sont en **entiers FCFA** et toujours calculés côté serveur.
6. Une fonctionnalité n'est « terminée » qu'avec ses tests, sa documentation et sa DoD
   (`docs/BACKLOG_MVP.md` §5).
