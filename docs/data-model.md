# Modèle de données — KEMTA SUIVI (Phase 0)

Statut : proposition à valider. Devise par défaut : **XAF (FCFA)**. Fuseau : `Africa/Douala`.
Toutes les dates sont en `UTC` en base, affichées en heure locale.

## Conventions transverses

| Sujet | Décision |
|---|---|
| Clé primaire | `UUID` (`uuid4`) exposé publiquement ; pas d'ID séquentiel dans les URLs |
| Suppression | **suppression logique** : champ `deleted_at` + manager `objects = SoftDeleteManager()` ; les journaux (`ActivityLog`) ne sont jamais supprimés |
| Horodatage | `created_at`, `updated_at` automatiques |
| Traçabilité | `created_by` / `updated_by` (FK `User`, `on_delete=PROTECT`) sur les entités métier |
| Argent | `DecimalField(max_digits=15, decimal_places=0)` — **montants entiers en FCFA** ; tout montant avec centimes est **refusé** (`400 amount_invalid`), pas arrondi silencieusement. Le jour où des centimes sont nécessaires, migration explicite + ADR. |
| Devise | `currency` = `XAF` par défaut, champ conservé pour évolutivité |
| GPS | `DecimalField(max_digits=9, decimal_places=6)` + `gps_accuracy` (mètres) + `gps_status` (`AVAILABLE` / `DENIED` / `UNAVAILABLE`) : le refus est un état explicite, pas une valeur nulle |
| Statuts | `TextChoices` côté serveur, jamais libre côté client ; les transitions sont validées par le backend |
| Contraintes | unicité partielle sur `deleted_at IS NULL` (ex. un numéro de téléphone libéré après suppression logique peut être réutilisé) |

---

## 1. Identité, authentification, journalisation

### `User`

| Champ | Type | Contraintes |
|---|---|---|
| `id` | UUID PK | |
| `phone` | `CharField(20)` | **unique** (si non supprimé), format E.164 (`+2376XXXXXXXX`) |
| `email` | `EmailField` | **nullable**, unique si renseigné, non requis à l'inscription |
| `email_verified_at` | `DateTime` | nullable |
| `password` | hash PBKDF2/Argon2 | jamais exposé |
| `first_name`, `last_name` | `CharField` | obligatoires |
| `role` | `CharField` | rôle global, voir `docs/rbac-matrix.md` (9 rôles) |
| `is_active` | bool | `False` tant que l'OTP d'activation n'est pas validé |
| `is_phone_verified` | bool | |
| `language` | `CharField(5)` | `fr` par défaut |
| `last_login_ip`, `last_login_at` | | |
| `password_changed_at` | `DateTime` | permet d'invalider les JWT émis avant un reset |
| `failed_login_count`, `locked_until` | | verrouillage temporaire anti brute-force |
| `deleted_at` | `DateTime` | nullable |

### `OTPCode` — **aucun OTP en clair**

| Champ | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `phone` | `CharField(20)` | indexé |
| `code_hash` | `CharField(128)` | `sha256(sel + code)` — le code en clair n'est jamais persisté |
| `salt` | `CharField(64)` | |
| `purpose` | `SIGNUP` / `PASSWORD_RESET` / `EMAIL_VERIFY` | un code n'est valable que pour son usage |
| `expires_at` | `DateTime` | `OTP_TTL_SECONDS` (défaut 300 s) |
| `attempts` | int | max `OTP_MAX_ATTEMPTS` (5) |
| `consumed_at` | `DateTime` | usage unique |
| `ip_address`, `user_agent` (tronqué) | | anti-abus + journalisation |
| `created_at` | | |
| `user` | FK nullable | renseigné si le compte existe |

Index : `(phone, purpose, consumed_at, expires_at)`. Purge par tâche Celery quotidienne.

### `ActivityLog` — **immuable**

`id` · `actor` (FK nullable, `PROTECT`) · `action` (choix fermé, voir §6) · `entity_type` ·
`entity_id` · `project` (FK nullable) · `organization` (FK nullable) · `metadata` (JSON :
ancienne/nouvelle valeur pour le financier) · `ip_address` · `user_agent` · `created_at`.

Règles : **aucune suppression ni modification** au niveau application (`ActivityLog.objects.delete()`
désactivé, queryset de lecture seule) ; lecture réservée aux rôles autorisés ; pagination
obligatoire.

### `IdempotencyKey`

`key` (UUID client) · `user` · `endpoint` · `request_hash` · `response_status` ·
`response_body` · `state` (`IN_PROGRESS` / `DONE`) · `created_at` (TTL 24 h).
Contrainte : `unique(user, endpoint, key)` → garantit « une même opération répétée ne crée pas de
doublon » (MVP-009).

---

## 2. Organisations, projets, membres (Phase 3)

### `Organization`
`id` · `name` · `slug` (unique) · `type` (`PROMOTER` / `PME` / `ENGINEERING_FIRM` /
`INVESTOR` / `PUBLIC`) · `country` (`CM`) · `city` · `owner` (FK `User`) · `is_active` ·
`deleted_at`.

### `OrganizationMember`
`organization` · `user` · `role` · `is_active` · `created_at`.
Contrainte : `unique(organization, user)`.

### `Project`
`id` · `organization` (FK, **obligatoire**) · `name` · `code` (unique par organisation) ·
`description` · `location_label` · `city` · `region` · `latitude` · `longitude` ·
`geofence_radius_m` (détection « preuve hors périmètre ») · `currency` (`XAF`) ·
`budget_total` · `status` (`DRAFT` / `ACTIVE` / `ON_HOLD` / `COMPLETED` / `ARCHIVED`) ·
`progress` (calculé serveur, mis en cache) · `planned_start_date` · `planned_end_date` ·
`actual_start_date` · `actual_end_date` · `cover` · `created_by` · `deleted_at`.

### `ProjectMember`
`project` · `user` · `role` (rôle *par projet*) · `can_validate_evidence` ·
`can_manage_finance` · `is_active` · `created_at`.
Contrainte : `unique(project, user)`.

---

## 3. Jalons et tâches (Phase 4)

### `Milestone`
`project` · `title` · `description` · `status` (`PLANNED` / `IN_PROGRESS` / `DONE` / `BLOCKED` /
`CANCELLED`) · `planned_date` · `actual_date` · `order` · `weight` (pondération de l'avancement) ·
`created_by` · suppression logique (`deleted_at`).

### `Task`
`project` · `milestone` (FK nullable) · `title` · `description` · `status` (`TODO` / `IN_PROGRESS` /
`DONE` / `BLOCKED` / `CANCELLED`) · `planned_start_date` · `planned_end_date` · `actual_start_date` ·
`actual_end_date` · `progress` (0-100) · `weight` · `assignee` (FK `User` nullable) ·
`depends_on` (M2M vers `Task`, sans cycle) · `created_by` · suppression logique (`deleted_at`).

Implémentation : `apps/projects/models.py`, calcul dans `apps/projects/progress.py`.

**Règles serveur** (détaillées dans `docs/flows/planning.md`)
- `planned_start_date <= planned_end_date`, sinon `400` avec le champ `planned_end_date`.
- `actual_date` / `actual_end_date` interdites si le statut n'est pas terminal, et obligatoires
  pour `DONE`.
- `progress` borné 0-100 (deux décimales), `weight` strictement positif.
- Avancement du projet = moyenne pondérée des jalons non annulés (poids `weight`) + un groupe
  « tâches sans jalon » ; recalculé après chaque écriture de jalon ou de tâche et persisté,
  jamais accepté depuis le client.
- Tâche en retard = `planned_end_date < aujourd'hui` **et** `status != DONE/CANCELLED`.
- Les dépendances (`depends_on`) sont un graphe acyclique interne au projet (`409` sinon).

---

## 4. Preuves terrain (Phase 5 — MVP-007, 008 ; Phase 6 — MVP-009)

### `Evidence`
`id` · `project` · `author` (`User`) · `task` (nullable) · `file` · `thumbnail` (WebP 320 px,
qualité 75) · `list_version` (JPEG 1080 px pour les listes) · `hash_sha256` (indexé) ·
`captured_at` (horodatage appareil) · `received_at` (serveur, `auto_now_add`) · `latitude` ·
`longitude` · `gps_accuracy` · `gps_status` (`AVAILABLE` / `UNAVAILABLE` / `DENIED`) ·
`device_model` · `device_platform` · `app_version` · `description` · `size_bytes` ·
`content_type` · `sync_status` (`PENDING` / `UPLOADING` / `SYNCED` / `FAILED` / `CONFLICT`) ·
`status` (`PENDING` / `VALIDATED` / `REJECTED` / `FLAGGED`) · `idempotency_key` · `deleted_at`.

Contraintes :
- `UniqueConstraint(project, hash_sha256)` **partielle** (hors preuves supprimées) → dédoublonnage
  à l'échelle du projet ; un doublon renvoie `409 duplicate_evidence` avec l'existante ;
- `UniqueConstraint(author, idempotency_key)` → rejouer un envoi interrompu renvoie **la même**
  preuve (`200` + `Idempotency-Replayed: true`) au lieu d'en créer une seconde ;
- index `(project, status, deleted_at)` et `(author, created_at)` pour la galerie et la file
  d'attente du validateur.

Règles de capture :
- le type du fichier est établi sur son **contenu réel** (magic bytes Pillow) — le nom et le
  `Content-Type` envoyés par l'appareil ne font pas foi ; JPEG/PNG/WebP, ≤ 10 Mo, ≤ 4000 px ;
- le chemin de stockage est **régénéré** côté serveur
  (`evidences/{project_id}/{yyyy}/{mm}/{uuid}.ext`) ;
- `hash_sha256` est l'empreinte du fichier **reçu** (recalculée serveur) ; le client calcule la
  même empreinte avant envoi pour que le doublon soit détecté même hors ligne (Phase 6) ;
- `captured_at` ne peut pas être dans le futur (`captured_at_in_future`) ;
- si le projet a des coordonnées et que le GPS est disponible, `distance_from_site_m` est calculée
  (Haversine) et `inside_geofence` reflète le périmètre du projet ; l'upload hors périmètre est
  refusé en `422 evidence_out_of_geofence` quand `EVIDENCE_GEOFENCE_ENFORCE` est actif ;
- `sync_status` est renseigné côté serveur (`SYNCED` à la réception) : la file locale (IndexedDB)
  reste la source de vérité de l'appareil jusqu'à confirmation (Phase 6).

### `EvidenceValidation`
`evidence` · `actor` · `from_status` · `to_status` · `action` (`VALIDATE` / `REJECT` / `FLAG` /
`REOPEN`) · `comment` (obligatoire pour `REJECT` et `FLAG`) · `created_at`.
Historique **append-only** : `save()` sur une ligne existante ou `delete()` lèvent une erreur,
y compris depuis l'administration. Transitions autorisées : `PENDING → {VALIDATED, REJECTED,
FLAGGED}`, `VALIDATED → {FLAGGED, REJECTED, PENDING}`, `REJECTED → {PENDING, VALIDATED}`,
`FLAGGED → {PENDING, VALIDATED, REJECTED}`. Un acteur ne valide jamais sa propre preuve
(`cannot_validate_own_evidence`, sauf administrateur plateforme).

### `SyncOperation` (Phase 6 — MVP-009)
`user` · `idempotency_key` · `operation_type` · `status` (`IN_PROGRESS` / `DONE`) · `http_status`
· `entity_type` · `entity_id` · `response_body` · `created_at` · `updated_at`.

Registre d'idempotence du lot de synchronisation : `unique(user, idempotency_key)`. Une clé `DONE`
est rejouée avec sa réponse d'origine (marquée `replayed: true`) ; une clé `IN_PROGRESS` renvoie
`409 op_in_progress` ; une clé utilisée pour un autre type d'opération renvoie
`409 idempotency_key_conflict`. Les refus métier libèrent la clé (l'utilisateur peut corriger et
relancer) : le registre ne garde donc trace que de ce qui a réellement été appliqué. Aucune donnée
métier n'y est dupliquée — seulement de quoi identifier l'entité touchée.

Côté appareil (jamais en base), la file locale `outbox` (IndexedDB) porte la même clé
`idempotencyKey`, le binaire de la photo (en `ArrayBuffer`), les métadonnées, le statut local
(`PENDING` / `UPLOADING` / `SYNCED` / `FAILED` / `CONFLICT`), le nombre d'essais et l'échéance du
prochain essai (retry exponentiel).

---

## 5. Finances (Phase 7 — implémenté)

Règles métier détaillées : `docs/flows/finance.md`. Contrats d'API : `docs/api-contract.md` §7.

### `BudgetLine` (poste budgétaire — `apps/finance/models.py`)
`project` · `label` (**unique par projet**) · `category` (`MATERIALS` / `LABOUR` / `EQUIPMENT` /
`SUBCONTRACT` / `TRANSPORT` / `ADMIN` / `OTHER`) · `planned_amount` (entier ≥ 0) · `order` ·
`notes` · `created_by` · `created_at` / `updated_at` · `deleted_at` (**suppression logique**).

Invariant : la **somme des postes ≤ `Project.budget_total`** (vérifié à chaque création et
révision). Un poste portant des dépenses ne peut pas être supprimé.

### `Expense` (dépense)
`project` · `budget_line` (nullable, **même projet**) · `title` · `description` · `amount`
(entier > 0) · `currency` · `incurred_on` · `invoice_number` (**unique par projet si renseigné**) ·
`invoice_date` (≤ `incurred_on`) · `status` (`DRAFT` / `SUBMITTED` / `APPROVED` / `REJECTED` /
`PAID` / `CANCELLED`) · `receipt` + `receipt_hash` (SHA-256 du contenu réel) · `created_by` ·
`approved_by` / `approved_at` · `cancelled_at` · `deleted_at`.

`is_editable` = `DRAFT` | `SUBMITTED` | `REJECTED` : après approbation, la dépense est figée
(la correction passe par une annulation puis une nouvelle dépense).

### `Payment` (paiement)
`expense` · `amount` (entier > 0) · `paid_on` (non future) · `method` (`CASH` /
`BANK_TRANSFER` / `MOBILE_MONEY` / `CHEQUE`) · `reference` · `note` · `created_by` ·
`cancelled_at` / `cancelled_by`.

Invariant : `Σ paiements vivants ≤ Expense.amount` (revérifié **après** écriture, sous verrou).

### `FinancialTransaction` (grand livre — *append-only*)
`project` · `type` (`EXPENSE` / `PAYMENT` / `ADJUSTMENT` / `CANCELLATION`) · `direction`
(`DEBIT` / `CREDIT`) · `amount` · `expense` / `payment` / `budget_line` (FK nullable) ·
`balance_after` (**solde du budget après opération**) · `note` · `created_by` · `created_at`.

Immutabilité garantie à trois niveaux : `save()` d'une ligne existante, `delete()` de l'instance
et `update()`/`delete()` du queryset lèvent `IntegrityError`. Aucune écriture n'est jamais
supprimée ; une erreur se corrige par une **contre-écriture**.

**Règles serveur (implémentées dans `apps/finance/services.py`)**
- Toute écriture passe par un service : `transaction.atomic()` +
  `select_for_update()` sur la ligne projet (et sur la dépense pour un paiement).
- `committed = Σ(EXPENSE, DEBIT) − Σ(CANCELLATION d'une dépense)` + ajustements nets ;
  `paid = Σ(PAYMENT, DEBIT) − Σ(CANCELLATION d'un paiement)` ;
  `balance = Project.budget_total − committed` : calculés en SQL (`aggregate`), jamais depuis une
  valeur client (tout total reçu est **ignoré**).
- Le consommé par poste est agrégé en **une** requête pour toute la liste (`line_committed_map`) :
  les listes et la synthèse restent à nombre de requêtes constant (tests dédiés).
- Dépassement → `422 budget_exceeded` sauf `override_reason` motivé (≥ 10 caractères), alors
  journalisé ; franchissement des seuils 80 % / 100 % journalisé une seule fois.
- Le montant d'une dépense n'est engagé **qu'à l'approbation** ; un rejet ou une annulation
  n'écrit rien (ou libère par contre-écriture ce qui avait été engagé).

## 6. Notifications et exploitation (Phase 10/11)

`Notification` : `user` · `type` (`MILESTONE_VALIDATED` / `EXPENSE_SUBMITTED` /
`EVIDENCE_REJECTED` / `BUDGET_THRESHOLD_REACHED` / `PROJECT_DELAYED`) · `title` · `body` ·
`payload` (JSON) · `group_key` (regroupement) · `count` · `is_read` · `created_at`.

`CeleryTaskLog` (suivi) : `task_id` · `name` · `state` · `retries` · `error` · `created_at` ·
`updated_at`.

## 7. Événements journalisés (`ActivityLog.action`)

`USER_REGISTERED` · `OTP_SENT` · `OTP_VERIFIED` · `OTP_FAILED` · `OTP_RESEND` · `LOGIN_SUCCESS` ·
`LOGIN_FAILED` · `LOGOUT` · `TOKEN_REFRESHED` · `TOKEN_REFRESH_REJECTED` ·
`PASSWORD_RESET_REQUESTED` · `PASSWORD_RESET_FAILED` · `PASSWORD_RESET_CONFIRMED` ·
`PASSWORD_RESET_DENIED` · `PASSWORD_CHANGED` · `EMAIL_ADDED` · `EMAIL_VERIFIED` ·
`ORG_CREATED` · `ORG_UPDATED` · `PROJECT_CREATED` · `PROJECT_UPDATED` · `PROJECT_ARCHIVED` ·
`MEMBER_ADDED` · `MEMBER_ROLE_CHANGED` · `MEMBER_REMOVED` · `MILESTONE_CREATED` ·
`MILESTONE_UPDATED` · `MILESTONE_CLOSED` · `TASK_CREATED` · `TASK_UPDATED` · `TASK_CLOSED` ·
`EVIDENCE_CAPTURED` · `EVIDENCE_VALIDATED` · `EVIDENCE_REJECTED` · `EVIDENCE_FLAGGED` ·
`EVIDENCE_REOPENED` ·
`BUDGET_LINE_CREATED` · `BUDGET_LINE_UPDATED` · `BUDGET_LINE_DELETED` · `EXPENSE_CREATED` ·
`EXPENSE_UPDATED` · `EXPENSE_SUBMITTED` · `EXPENSE_APPROVED` · `EXPENSE_REJECTED` ·
`EXPENSE_CANCELLED` · `EXPENSE_RECEIPT_ATTACHED` · `PAYMENT_RECORDED` · `PAYMENT_CANCELLED` ·
`ADJUSTMENT_RECORDED` · `BUDGET_THRESHOLD_REACHED` · `BUDGET_EXCEEDED` · `EXPORT_GENERATED`.

## 8. Diagramme relationnel (texte)

```
Organization ──< OrganizationMember >── User
     │
     └──< Project ──< ProjectMember >── User
              ├──< Milestone ──< Task ──< Task (depends_on)
              ├──< BudgetLine ──< Expense ──< Payment
              ├──< FinancialTransaction
              └──< Evidence ──< EvidenceValidation
User ──< OTPCode        User ──< ActivityLog (actor)      Project ──< ActivityLog
User ──< IdempotencyKey User ──< Notification
```
