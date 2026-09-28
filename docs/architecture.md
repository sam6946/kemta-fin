# Architecture initiale — KEMTA SUIVI (Phase 0/1)

## 1. Vue d'ensemble

```
┌────────────────────────┐        HTTPS         ┌──────────────────────────┐
│  Mobile / Web (PWA)    │ ───────────────────► │  Nginx (TLS, gzip,       │
│  React + TS + Vite     │                      │  static frontend, /api)  │
│  IndexedDB + file sync │ ◄─────────────────── │                          │
└────────────────────────┘                      └───────────┬──────────────┘
                                                            │
                          ┌─────────────────────────────────┴────────────────┐
                          │  Django 5 + DRF (Gunicorn, workers sync/threads) │
                          │  apps: users · projects · evidence · finance ·   │
                          │        core (activity, health, meta)             │
                          └───┬──────────────┬──────────────┬────────────────┘
                              │              │              │
                     ┌────────▼───┐   ┌──────▼──────┐  ┌───▼──────────────┐
                     │ PostgreSQL │   │ Redis       │  │ Celery worker    │
                     │ 16 (données│   │ (cache,     │  │ + beat (médias,  │
                     │ + fichiers │   │  broker, RL)│  │  notifications,  │
                     │  de ledger)│   │             │  │  purge OTP)      │
                     └────────────┘   └─────────────┘  └──────────────────┘
                                                            │
                                              ┌─────────────▼──────────────┐
                                              │ Stockage média (volume     │
                                              │ chiffré / S3-compatible)   │
                                              └────────────────────────────┘
```

**Choix structurants**

| Sujet | Décision | Raison |
|---|---|---|
| Backend | Django 5 + DRF, apps par domaine | admin gratuit, ORM solide, `transaction.atomic()`, écosystème auth |
| Base | PostgreSQL 16 | contraintes d'unicité partielle, `select_for_update`, JSONB |
| Cache / broker | Redis 7 | un seul composant pour cache, rate limiting et Celery |
| Async | Celery (worker + beat) | médias, SMS, notifications, purge OTP |
| Frontend | React 18 + TypeScript + Vite, PWA | offline-first (`idb` : file IndexedDB + binaire des photos), code splitting |
| API | REST JSON (pas de GraphQL) | simplicité, cache HTTP, contrôle fin des permissions |
| Auth | JWT (access 15 min + refresh 7 j rotatif) + OTP SMS | conforme aux flux du backlog |

## 2. Docker Compose (développement)

Services : `web` (Gunicorn/Django) · `worker` (Celery) · `beat` · `db` (postgres:16-alpine) ·
`redis` (redis:7-alpine) · `frontend` (Vite dev server, HMR) · `nginx` (profils
dev+prod). Un seul `docker compose up` démarre tout ; `docker compose exec web python manage.py
migrate` applique les migrations sur une base vide.

Volumes : `pgdata` (données), `media` (fichiers uploadés en dev). En production, le volume
`media` est remplacé par un bucket S3-compatible (voir §4).

Fichiers : `docker-compose.yml` (base) · `docker-compose.dev.yml` · `docker-compose.prod.yml` ·
`backend/Dockerfile` · `frontend/Dockerfile` · `.env.example` (jamais de valeurs réelles).
Les secrets ne sont **jamais** dans l'image : `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
clés du fournisseur SMS viennent de l'environnement (ou d'un gestionnaire de secrets) et
l'absence de `SECRET_KEY` en production fait **échouer le démarrage** (fail-fast).

## 3. Configuration

`config/settings.py` piloté par variables d'environnement (django-environ) :
`DJANGO_ENV` (`local`|`test`|`production`) · `DEBUG` · `ALLOWED_HOSTS` (ajout automatique du
domaine de prévisualisation) · `DATABASE_URL` · `REDIS_URL` · `CELERY_BROKER_URL` ·
`SECRET_KEY` · `SMS_PROVIDER` (`console` en dev/test, `real` en prod) · `OTP_*` · `CORS_ALLOWED_ORIGINS` ·
`MEDIA_STORAGE` (`local`|`s3`).

En test/CI : base SQLite (`USE_SQLITE=1`), cache `LocMem`, `CELERY_TASK_ALWAYS_EAGER`, SMS en
adaptateur console → les tests tournent sans infrastructure externe.

## 4. Stratégie de stockage des médias

- **Upload** : `multipart` direct vers Django (`/api/evidences/`), en-tête `Idempotency-Key`.
  Pas de presigned URL dans le MVP (simplicité) — évolution documentée en ADR-004.
- **Validation serveur** : type MIME réel (magic bytes via Pillow), taille max 10 Mo, dimensions
  max 4000 px ; refus → `415`/`413`. Le nom de fichier client est ignoré, le chemin de
  stockage est régénéré (`evidences/{project_id}/{yyyy}/{mm}/{uuid}.{ext}`).
- **Dérivées** : à l'upload, l'originale est conservée et une **miniature** (320 px, WebP q75)
  + une version « liste » (1080 px) sont générées **en tâche Celery**, jamais dans la requête
  HTTP. Les listes n'utilisent que les thumbnails.
- **Accès** : les médias ne sont **pas** servis publiquement. Les fichiers passent toujours par
  l'API (`/api/evidences/{id}/file/`, `/thumbnail/`) qui vérifie l'appartenance au projet
  (`404` sinon) et répond en `Cache-Control: private`. Avec `MEDIA_X_ACCEL_REDIRECT=true`, Django
  renvoie un `X-Accel-Redirect` et c'est Nginx qui sert le fichier — le contrôle d'accès reste
  dans l'application. Les URLs renvoyées sont **relatives** pour rester valables derrière un proxy.
  Implémenté et testé en phase 5 (ADR-004 et ADR-006 tranchées).
- **Nommage/version** : `Evidence.hash_sha256` permet la déduplication ; aucun fichier orphelin
  (tâche de nettoyage hebdomadaire).

## 5. Observabilité

- **Logs structurés** : JSON sur une ligne (`timestamp`, `level`, `logger`, `request_id`, `user_id`,
  `path`, `status`, `duration_ms`, `message`) en production ; format lisible en dev.
  Interdiction absolue de journaliser : mot de passe, code OTP, hash, token, fichier. Un test
  (`test_no_secrets_in_logs`) grepe les logs de chaque parcours d'auth.
- **Healthcheck** : `GET /api/health/` vérifie app + base (`SELECT 1`) + Redis (`PING`), avec
  `checks_ms` ; `503` si une dépendance critique échoue. Utilisé par Docker `healthcheck` et
  par le supervisseur de production.
- **Métriques** (compteurs, exposés via `/api/metrics/` protégé ou export Prometheus ultérieur) :
  requêtes par endpoint/latence/statut · `otp_sent_total`, `otp_failed_total` ·
  `password_reset_total{success,failed}` · `upload_total`, `upload_bytes` ·
  `sync_failed_total` · `celery_task_total{state}`.
- **Erreurs** : handler DRF unique → enveloppe d'erreur + `request_id` ; exception non gérée →
  `500` générique + log `ERROR` avec `request_id` (aucune donnée sensible dans la réponse).

## 6. Hors ligne (phase 6)

- La file d'opérations et les photos en attente vivent dans **IndexedDB** (`idb`), avec un repli
  mémoire si le navigateur refuse le stockage persistant — l'écran de suivi l'annonce alors
  explicitement au lieu de laisser croire à une sauvegarde.
- Le binaire des photos est stocké en `ArrayBuffer` (et non en `Blob`) : c'est la forme la plus
  universellement clonable par IndexedDB ; il est reconstruit en `Blob` à l'envoi.
- Aucune donnée du serveur n'est mise en cache pour l'instant : le cache de lecture (SWR) arrive
  avec le dashboard agrégé (phase 8). Ce qui est stocké localement est **exactement** ce qui doit
  être rejoué.
- Toute écriture rejouable porte une clé d'idempotence ; le serveur tient le registre
  (`SyncOperation`) et rejoue la réponse d'origine plutôt que de réappliquer l'opération.

## 7. Sécurité (exigences transverses)

- Secrets uniquement par variables d'environnement ; `.env.example` documenté, `.env` ignoré.
- Cookies de refresh : `HttpOnly`, `Secure`, `SameSite=Strict` en production ; access token en
  mémoire côté client.
- Rate limiting sur auth/OTP/reset (voir `api-contract.md` §1).
- Mot de passe : validateurs Django (longueur, similitude, mots de passe courants) + refus de la
  réutilisation du mot de passe courant ; OTP haché, expiré, à usage unique.
- `SECURE_*`, `HSTS`, `X_FRAME_OPTIONS`, `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS` activés en
  production ; `DEBUG=False` obligatoire en production (assertion au démarrage).
- Dépendances : `pip-audit`/`npm audit` en CI, blocage sur vulnérabilité haute.

## 8. Décisions d'architecture (ADR)

| # | Décision | Statut | Responsable | Échéance |
|---|---|---|---|---|
| ADR-001 | Téléphone = identifiant unique, email facultatif | Acceptée | Produit | — |
| ADR-002 | Réinitialisation du mot de passe par OTP SMS (P0) | Acceptée | Produit/Tech | — |
| ADR-003 | Montants en entiers FCFA, pas de centimes | Acceptée | Finance/Tech | — |
| ADR-004 | Upload direct Django vs presigned URL S3 | Acceptée (upload direct) | Tech | ✅ Phase 5 |
| ADR-005 | Fournisseur SMS (local vs international) + coût/OTP | **Ouverte** | Produit | avant Phase 2 |
| ADR-006 | Stockage média : volume chiffré vs S3-compatible | Acceptée (volume privé + `X-Accel-Redirect`) | Tech | ✅ Phase 5 |
| ADR-007 | Procédure « numéro perdu / changement de SIM » | **Ouverte** | Produit | avant Phase 2 |
| ADR-008 | SSE/WebSocket pour le temps réel (post-MVP) | Reportée | Tech | post-MVP |

## 9. Arborescence cible

```
backend/     config/ (settings, urls, celery, asgi/wsgi) · apps/ (users, projects, evidence,
             finance, core) · requirements*.txt · manage.py · pytest.ini
frontend/    src/ (api, auth, components, pages, lib (file hors ligne `db.ts`/`outbox.ts`), sync/) · vite.config.ts
infra/       docker-compose*.yml · nginx/ · Dockerfile.*
docs/        BACKLOG_MVP.md · architecture.md · data-model.md · rbac-matrix.md ·
             api-contract.md · offline-sync.md · test-plan.md · flows/authentication.md
```
