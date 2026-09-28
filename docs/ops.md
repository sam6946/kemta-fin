# Exploitation — files asynchrones, tâches, métriques, sauvegardes (phases 10 et 11)

Ce document est le **runbook** de KEMTA SUIVI : que surveiller, que faire quand une tâche échoue,
comment protéger le journal d'activité et comment sauvegarder.

## 1. Architecture d'exécution

| Service | Rôle | Commande |
|---|---|---|
| `web` | API Django (gunicorn, 3 workers) | `gunicorn config.wsgi:application` |
| `worker` | tâches Celery (SMS/email, notifications, vignettes, entretien) | `celery -A config worker -Q celery --concurrency 2 --max-tasks-per-child 200` |
| `beat` | planificateur (tâches périodiques) | `celery -A config beat` |
| `redis` | broker Celery (base 0), cache + compteurs de métriques (base 1) | |
| `db` | PostgreSQL 16 | |
| `frontend` | Nginx : SPA, TLS, proxy API, médias protégés | `frontend/nginx.prod.conf` |

Une seule file, `celery`. Si le volume l'exige, séparer une file `critical` (OTP, notifications)
d'une file `bulk` (vignettes, entretien) se fait sans changer le code métier : `queue=` sur les
tâches et deux workers dédiés.

### Tâches planifiées (`CELERY_BEAT_SCHEDULE`, heure UTC ; Douala = UTC+1)

| Tâche | Fréquence | Effet |
|---|---|---|
| `users.purge_expired_otps` | toutes les 6 h | supprime les OTP expirés |
| `notifications.detect_project_delays` | 05:00 (06:00 Douala) | détecte les retards, émet `ProjectDelayed` (une seule notification groupée par projet et par jour) |
| `notifications.purge_old_notifications` | dimanche 03:30 | supprime les notifications **lues** de plus de `NOTIFICATION_RETENTION_DAYS` (90) |
| `core.cleanup_temp_files` | 02:15 | nettoie `MEDIA_ROOT/tmp` (> `TEMP_FILE_MAX_AGE_HOURS`) |
| `core.purge_old_task_runs` | 03:00 | purge le suivi des tâches (`TASK_RUN_RETENTION_DAYS`, 14 j ; les échecs sont gardés 4 fois plus longtemps) |

## 2. Événements métier et notifications

Les cinq événements du backlog sont émis **après validation de la transaction** (`on_commit`),
donc jamais pour une écriture annulée. La tâche `notifications.process_domain_event` les
transforme en notifications in-app (destinataires calculés en un jeu de requêtes, l'auteur de
l'action n'est jamais notifié).

| Événement | Émis par | Destinataires |
|---|---|---|
| `MilestoneValidated` | jalon passé à `DONE` | tous les membres du projet + propriétaire d'organisation |
| `ExpenseSubmitted` | `transition_expense` (SUBMIT) | propriétaire de projet, financier |
| `EvidenceRejected` | rejet d'une preuve | propriétaire de projet, ingénieur |
| `BudgetThresholdReached` | franchissement de 80 % puis 100 % du budget | propriétaire, financier, investisseur |
| `ProjectDelayed` | tâche quotidienne | propriétaire de projet, ingénieur |

**Regroupement** : les notifications non lues d'un même utilisateur et d'un même type/projet sont
fusionnées (`group_key`, compteur `count`, 10 éléments récents conservés) — contrainte d'unicité
partielle en base, donc sûre en cas de concurrence. La déduplication (`dedupe_key`) rend le
rejeu d'une tâche sans effet : un retry ne crée jamais de doublon.

## 3. Retry, échecs et suivi des tâches

* `process_domain_event` : tâche **critique**, `acks_late=True`, 5 relances avec attente
  exponentielle (10 s, 20 s, 40 s, 80 s, 160 s). Au-delà, l'échec est journalisé et visible.
* Envoi SMS/email : 3 relances, 60 s.
* Tous les états passent par les signaux Celery (`apps/core/task_tracking.py`) vers la table
  **`TaskRun`** : `STARTED`, `SUCCESS`, `RETRY`, `FAILURE`, avec nombre de tentatives, durée et
  message d'erreur **tronqué et sans arguments** (ils peuvent contenir un numéro ou un OTP).
* Une panne du suivi lui-même ne casse jamais la tâche.

### Que faire

| Symptôme | Diagnostic | Action |
|---|---|---|
| Notifications absentes | `GET /api/ops/status/` → file `celery` qui grossit, worker absent | `docker compose ps worker`, relancer ; les tâches en file sont conservées par Redis |
| `FAILURE` sur `process_domain_event` | table `TaskRun` (admin Django) : `error` | corriger la cause (base, données), puis rejouer depuis un `manage.py shell` : `process_domain_event.delay(<même charge utile>)` (le `dedupe_key` évite tout doublon) |
| Vignettes manquantes | `TaskRun` de `apps.evidences.tasks.*` en échec | les listes retombent sur le fichier d'origine (`/api/evidences/<id>/file/`) : aucune perte, seulement plus lourd |
| Redis indisponible | `/api/health/` → `redis: down` | l'API reste utilisable : le cache et les métriques échouent en silence (les limiteurs échouent ouverts), les tâches sont mises en file dès le retour de Redis |
| Pic de 429 | `kemta_rate_limited_total` par route | attaque sur l'OTP/login, ou quota trop bas pour un site derrière un NAT : ajuster `THROTTLE_*` |

## 4. Observabilité

* `GET /api/health/` — sonde de vivacité (base, Redis, version), exemptée de redirection HTTPS.
* `GET /api/ops/metrics/` — format **Prometheus**. Authentification : `Authorization: Bearer
  $METRICS_TOKEN` (scraper) **ou** compte avec la capacité `view_operations`. Sans jeton configuré,
  seuls les administrateurs y accèdent. Nginx **ne l'expose pas** (404) : le scraper interroge
  `web:8000` dans le réseau Docker.
* `GET /api/ops/status/` — synthèse JSON : base, cache, longueur des files, exécutions de tâches,
  échecs récents.

| Métrique | Question à laquelle elle répond |
|---|---|
| `kemta_http_requests_total`, `_errors_total`, `_request_duration_seconds` | temps de réponse et taux d'erreur par route (gabarit d'URL, jamais l'identifiant ; 5xx comptés à part) |
| `kemta_uploads_total`, `kemta_upload_bytes_total` | volume et refus d'envois (type, taille) |
| `kemta_sync_operations_total`, `kemta_sync_errors_total` | opérations hors ligne appliquées, conflits, échecs |
| `kemta_celery_tasks_total`, `kemta_celery_task_duration_seconds` | santé des tâches |
| `kemta_domain_events_total`, `kemta_notifications_total` | événements métier émis, notifications créées/regroupées |
| `kemta_rate_limited_total` | requêtes refusées (429) par route |

Les compteurs sont partagés entre workers via Redis (`METRICS_BACKEND=redis`). Aucune donnée
personnelle ni montant dans les étiquettes. Les requêtes plus lentes que `SLOW_REQUEST_SECONDS`
(1,5 s) sont journalisées en JSON avec le `request_id`.

### Alertes recommandées

| Condition | Gravité |
|---|---|
| `rate(kemta_http_errors_total[5m]) / rate(kemta_http_requests_total[5m]) > 2 %` (`_errors_total` ne compte que les 5xx) | page |
| p95 de `kemta_http_request_duration_seconds` > 1 s pendant 10 min | ticket |
| file `celery` > 500 tâches ou worker absent > 5 min | page |
| au moins un `FAILURE` de `process_domain_event` en 1 h | ticket |
| `kemta_sync_errors_total` en hausse soutenue | ticket (probable régression client) |
| `kemta_rate_limited_total{route=~".*/auth/login/"}` > 100/h | sécurité |

## 5. Journal d'activité : protection en production

L'application interdit déjà toute modification ou suppression (modèle, queryset, admin,
`IntegrityError`). En **production PostgreSQL**, ajouter la garantie côté base : le rôle
applicatif ne doit pas pouvoir modifier l'historique, même en cas de faille applicative.

```sql
-- Exécuté par le propriétaire de la base, une fois les migrations appliquées.
-- `kemta_app` = rôle utilisé dans DATABASE_URL par web/worker/beat.
REVOKE UPDATE, DELETE, TRUNCATE ON core_activitylog FROM kemta_app;
REVOKE UPDATE, DELETE, TRUNCATE ON finance_financialtransaction FROM kemta_app;
REVOKE UPDATE, DELETE, TRUNCATE ON evidences_evidencereview FROM kemta_app;
```

À rejouer après chaque `migrate` qui recrée une table (les droits suivent la table). Ces
REVOKE ne sont **pas** une migration Django : les tests tournent sous SQLite et la suppression
d'un utilisateur ou d'un projet doit rester possible pour le propriétaire de la base
(archivage légal). La purge du journal, si la loi l'impose, se fait par une procédure
d'administration tracée, hors application.

## 6. Sauvegardes et restauration

| Donnée | Méthode | Fréquence | Rétention |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc` chiffré (ou sauvegarde continue WAL) vers un stockage distant | quotidienne (+ WAL si disponible) | 30 j quotidiennes, 12 mensuelles |
| Médias (`/app/var/media`) | instantané du volume ou `rsync` incrémental chiffré | quotidienne | 30 j |
| Redis | non critique (broker + cache) ; l'AOF/RDB évite de perdre les tâches en file | — | — |
| Secrets (`.env`) | gestionnaire de secrets de l'hébergeur ; jamais dans Git | à chaque changement | — |

Restauration : (1) restaurer la base, (2) restaurer les médias, (3) `python manage.py migrate`,
(4) rejouer les `REVOKE` du §5, (5) vider le cache Redis (`FLUSHDB` sur la base 1) pour éviter
un dashboard périmé — le cache est de toute façon versionné par projet et expire en 60 s.
**Testez une restauration complète avant la mise en production**, puis chaque trimestre.

## 7. Mise en production — liste de contrôle

1. `.env` de production : `DJANGO_ENV=production`, `DEBUG=false`, `SECRET_KEY` aléatoire (≥ 50
   caractères), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, mot de passe
   PostgreSQL fort, `METRICS_TOKEN`, `SMS_PROVIDER=real` (+ clé), `SENTRY_DSN` si utilisé.
2. Certificats TLS dans `TLS_CERTS_DIR` (`fullchain.pem`, `privkey.pem`).
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` (Compose ≥ 2.24).
4. `docker compose exec web python manage.py check --deploy` → aucun avertissement.
5. Appliquer les `REVOKE` du §5.
6. Ne **jamais** lancer `seed_dev` ni `seed_loadtest` (refusés en production sauf `--force`).
7. `python loadtest/run.py` contre la **préproduction** (pas la production) : voir `docs/performance.md`.
