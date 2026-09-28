# Flux d'authentification — inscription, OTP, connexion, réinitialisation du mot de passe

Statut : proposition de cadrage (Phase 0). Document vivant, à valider avant Phase 2.
Référence backlog : `docs/BACKLOG_MVP.md` (MVP-001, MVP-002, MVP-003, **MVP-017**).

## 0. Modèle d'authentification retenu

- **Identifiant principal : le numéro de téléphone** (normalisé au format E.164, ex. `+2376XXXXXXXX`).
- **L'email est facultatif** et ajoutable depuis le dashboard ; il n'est jamais requis à l'inscription.
- **Second facteur / preuve de possession du numéro : l'OTP SMS**, utilisé pour :
  - l'activation du compte à l'inscription (`purpose = SIGNUP`) ;
  - la réinitialisation du mot de passe (`purpose = PASSWORD_RESET`) ;
  - la vérification d'un email ajouté ultérieurement (`purpose = EMAIL_VERIFY`, MVP-018).
- **Connexion : téléphone + mot de passe** → access token JWT court + refresh token rotatif.
- Conséquence directe : **le mot de passe est un secret que l'utilisateur peut oublier** → le
  parcours de réinitialisation (§4) fait partie du périmètre bloquant (P0).

### Règles OTP (communes à tous les usages)

| Règle | Valeur par défaut | Variable d'environnement |
|---|---|---|
| Longueur du code | 6 chiffres | `OTP_LENGTH` |
| Durée de validité | 300 s | `OTP_TTL_SECONDS` |
| Tentatives max par code | 5 | `OTP_MAX_ATTEMPTS` |
| Renvois max par numéro / heure | 3 | `OTP_RESEND_LIMIT_PER_PHONE` |
| Renvois max par IP / heure | 10 | `OTP_RESEND_LIMIT_PER_IP` |
| Délai minimal avant renvoi | 60 s | `OTP_RESEND_COOLDOWN_SECONDS` |
| Stockage | hash (`sha256(sel + code)`), jamais en clair | — |
| Usage | unique, lié à un `purpose` | — |

Le code est comparé en temps constant. Les OTP expirés sont purgés par une tâche Celery
(`purge_expired_otps`, quotidienne).

---

## 1. Inscription (MVP-001 + MVP-002)

```
Utilisateur                 Frontend                    Backend                     SMS
    |  saisie du numéro        |                            |                         |
    |------------------------->|  POST /auth/register/      |                         |
    |                          |  {phone, password,         |                         |
    |                          |   password_confirm,        |                         |
    |                          |   first_name, last_name}   |                         |
    |                          |--------------------------->|                         |
    |                          |                            | normalise le numéro     |
    |                          |                            | valide le format CM     |
    |                          |                            | refuse si déjà utilisé  |
    |                          |                            | crée user (is_active=0) |
    |                          |                            | génère OTP (hashé)      |
    |                          |                            |------------------------>| SMS "code: 123456"
    |                          |  201 {phone_masked,        |                         |
    |                          |        otp_ttl, retry_in}  |                         |
    |<-------------------------|                            |                         |
    |  saisie du code          |                            |                         |
    |------------------------->|  POST /auth/otp/verify/    |                         |
    |                          |  {phone, code}             |                         |
    |                          |--------------------------->| vérifie hash/TTL/tentatives
    |                          |  200 {access, refresh,     | is_active = 1           |
    |                          |        user}               | OTP consommé            |
```

**Réponses d'erreur**

| Cas | HTTP | Code d'erreur |
|---|---|---|
| Format de numéro invalide | 400 | `phone_invalid` |
| Numéro déjà utilisé (compte actif) | 409 | `phone_already_used` |
| Numéro déjà utilisé (compte non activé) | 409 | `phone_pending_activation` (propose renvoi d'OTP) |
| Mot de passe trop faible | 400 | `password_too_weak` |
| OTP incorrect / expiré / consommé | 400 | `otp_invalid` |
| Trop de tentatives | 429 | `otp_max_attempts` |
| Renvoi trop rapide ou quota dépassé | 429 | `otp_resend_limited` |

Aucune de ces réponses ne contient de code OTP, de hash, de mot de passe ou de donnée
personnelle d'un tiers.

---

## 2. Connexion et sessions (MVP-003)

- `POST /api/auth/login/` → `{phone, password}` → `200 {access, refresh, user, permissions}`.
- Compte non confirmé → `403 account_not_confirmed` (avec possibilité de renvoyer un OTP
  d'activation).
- `POST /api/auth/token/refresh/` → rotation du refresh token ; l'ancien refresh est révoqué.
- `POST /api/auth/logout/` → le refresh token est blacklisté (`OutstandingToken`/`Blacklist`).
- Access token : 15 min. Refresh token : 7 jours. Durées configurables.
- Le frontend stocke l'access token en mémoire et le refresh token en cookie `HttpOnly` +
  `Secure` + `SameSite=Strict` en production (pas de `localStorage` pour les tokens).
- En cas de `401` sur refresh, le frontend purge la session et redirige vers la connexion, sans
  boucle de retry (un seul essai de refresh par requête).

---

## 3. Ajout facultatif de l'email

- `POST /api/auth/email/request/` (authentifié) → OTP envoyé à l'email saisi
  (`purpose = EMAIL_VERIFY`).
- `POST /api/auth/email/confirm/` → email marqué vérifié.
- L'email vérifié devient un canal secondaire possible pour la réinitialisation (MVP-018).

---

## 4. Réinitialisation du mot de passe — « mot de passe oublié » (MVP-017)

### 4.1 Parcours utilisateur

1. Écran de connexion → lien **« Mot de passe oublié ? »** toujours visible.
2. Saisie du **numéro de téléphone** (aucun email demandé).
3. Réception d'un **OTP SMS** (`purpose = PASSWORD_RESET`).
4. Saisie du code + **nouveau mot de passe** (avec confirmation et indicateur de robustesse).
5. Succès → **révocation de toutes les sessions actives**, SMS de confirmation, redirection vers
   l'écran de connexion avec message « Votre mot de passe a été réinitialisé, connectez-vous ».

### 4.2 Endpoints

| Méthode | Endpoint | Authentification | Rôle |
|---|---|---|---|
| `POST` | `/api/auth/password/reset/request/` | aucune | Demande un OTP de réinitialisation |
| `POST` | `/api/auth/password/reset/confirm/` | aucune (OTP fait foi) | Valide l'OTP et définit le nouveau mot de passe |
| `POST` | `/api/auth/password/change/` | JWT requis | Change le mot de passe d'un utilisateur connecté (ancien mot de passe obligatoire) |

**`POST /api/auth/password/reset/request/`**

```json
{ "phone": "+2376XXXXXXXX" }
```

Réponse (toujours identique, y compris si le numéro est inconnu) :

```json
{ "detail": "Si ce numéro est associé à un compte, un code vient d'être envoyé par SMS.",
  "retry_in": 60, "otp_ttl": 300 }
```

- HTTP `200` dans tous les cas traitables, `429` si le quota par numéro/IP est dépassé.
- **Aucune énumération de compte** : même corps, même code HTTP, même temps de réponse
  (le traitement est symétrique : génération et hachage de l'OTP même pour un numéro inconnu ;
  un éventuel SMS n'est envoyé que si le compte existe).
- Si le compte existe mais n'est **pas actif**, on ne crée pas d'OTP de réinitialisation :
  on renvoie un OTP d'activation (`purpose = SIGNUP`) et la réponse reste neutre.
- Si le compte est **désactivé ou supprimé logiquement** : réponse neutre, aucun OTP, événement
  journalisé `PASSWORD_RESET_DENIED`.

**`POST /api/auth/password/reset/confirm/`**

```json
{ "phone": "+2376XXXXXXXX", "code": "123456", "new_password": "…", "new_password_confirm": "…" }
```

Réponse `200` :

```json
{ "detail": "Mot de passe réinitialisé.", "sessions_revoked": true }
```

Traitement serveur, dans une transaction :

1. récupération de l'OTP par `(phone, code_hash, purpose=PASSWORD_RESET)` non consommé et non
   expiré — sinon `400 otp_invalid` / `429 otp_max_attempts` ;
2. validation du nouveau mot de passe (politique serveur, voir §4.3) — sinon
   `400 password_too_weak` / `400 password_reused` ;
3. `user.set_password(new_password)`, `user.save()` ;
4. OTP marqué consommé (`consumed_at`) ;
5. **révocation de tous les refresh tokens** de l'utilisateur (`OutstandingToken` blacklistés) ;
6. incrément de `user.password_changed_at` / `token_version` si retenu ;
7. journalisation `PASSWORD_RESET_CONFIRMED` (acteur, IP, user-agent tronqué, date) ;
8. envoi asynchrone (Celery) du SMS de confirmation.

**`POST /api/auth/password/change/`** (utilisateur connecté)

```json
{ "current_password": "…", "new_password": "…", "new_password_confirm": "…" }
```

- Ancien mot de passe obligatoire (protection contre le détournement de session).
- Mêmes règles de politique de mot de passe et même révocation des autres sessions.

### 4.3 Politique de mot de passe (validée côté serveur)

- longueur minimale 10 caractères (configurable, `PASSWORD_MIN_LENGTH`) ;
- au moins une lettre et un chiffre ;
- refus des mots de passe figurant dans une liste de mots de passe courants
  (`django.contrib.auth.password_validation.CommonPasswordValidator`) ;
- refus d'un mot de passe trop similaire aux attributs de l'utilisateur (nom, téléphone) ;
- **interdiction de réutiliser le mot de passe actuel** ;
- historique des N derniers mots de passe (option P1) ;
- aucun mot de passe n'est jamais renvoyé par l'API, journalisé, ni envoyé par SMS/email.

### 4.4 Sécurité / anti-abus

- Rate limiting sur les trois endpoints (par IP et par numéro), plus strict que la moyenne
  (ex. 5 demandes/heure/numéro, 20/heure/IP).
- Blocage temporaire après `OTP_MAX_ATTEMPTS` échecs sur un même OTP.
- Compteur d'échecs de réinitialisation exposé en métrique (détection d'abus déterministe,
  pas de scoring IA).
- SMS de confirmation systématique après changement effectif (alerte l'utilisateur légitime).
- Journaux : `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET_FAILED`, `PASSWORD_RESET_CONFIRMED`,
  `PASSWORD_RESET_DENIED`, `PASSWORD_CHANGED` — sans secret, sans code, sans mot de passe.
- Pas de question secrète, pas de réinitialisation manuelle par le support dans le MVP
  (une procédure documentée d'escalade est suffisante).

### 4.5 États frontend à couvrir

loading (envoi du code, vérification) · empty (numéro non reconnu → message neutre) · error
(réseau, 429, OTP invalide, mot de passe refusé) · success (redirection connexion) · offline
(détection d'absence de réseau : la réinitialisation **nécessite** le réseau, l'écran l'indique
explicitement au lieu d'échouer silencieusement) · permissions (N/A).

> Note offline : contrairement à la capture de preuve, la réinitialisation du mot de passe ne
> peut pas être mise en file d'attente (elle dépend d'un SMS et d'une validation serveur
> immédiate). Le parcours est donc **en ligne uniquement**, et le cas « pas de réseau à cet
> instant » est géré comme une erreur explicite et réessayable, conformément à la règle
> « aucune action terrain *critique* ne dépend d'une requête immédiate » (MVP-009).

### 4.6 Tests à livrer

| Niveau | Cas |
|---|---|
| Unitaire | hachage de l'OTP, expiration, `purpose`, politique de mot de passe, révocation des tokens |
| API | numéro inconnu (réponse neutre), OTP invalide, OTP expiré, OTP réutilisé, OTP d'un autre `purpose`, dépassement de tentatives, renvoi limité, mot de passe faible, réutilisation du mot de passe courant, compte non confirmé, compte désactivé |
| Sécurité | absence d'énumération de compte (corps + code HTTP identiques), absence de secret dans les logs, rate limiting effectif, sessions invalidées après reset |
| E2E | « Mot de passe oublié ? » → OTP → nouveau mot de passe → reconnexion → ancien refresh token refusé |

---

## 5. Journalisation (rattaché à MVP-012)

Événements d'authentification enregistrés dans `ActivityLog` :

`USER_REGISTERED`, `OTP_SENT`, `OTP_VERIFIED`, `OTP_FAILED`, `OTP_RESEND`, `LOGIN_SUCCESS`,
`LOGIN_FAILED`, `LOGOUT`, `TOKEN_REFRESHED`, `TOKEN_REFRESH_REJECTED`,
`PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET_FAILED`, `PASSWORD_RESET_CONFIRMED`,
`PASSWORD_RESET_DENIED`, `PASSWORD_CHANGED`, `EMAIL_ADDED`, `EMAIL_VERIFIED`.

Chaque entrée contient : acteur, action, entité, identifiant d'entité, date, projet si
pertinent, IP et user-agent tronqué. Les événements critiques ne sont jamais supprimés
physiquement et ne sont consultables que par les rôles autorisés.

---

## 6. Questions ouvertes

| # | Question | Responsable | À résoudre avant |
|---|---|---|---|
| Q1 | Fournisseur SMS (local/AGR vs international) et coût par OTP | Produit | Phase 2 |
| Q2 | Fallback si le SMS n'arrive pas (réseau dégradé) : réessai, délai, canal alternatif | Tech | Phase 2 |
| Q3 | Numéro perdu / changement de SIM : procédure de reprise de compte (hors MVP, à documenter) | Produit | Phase 2 |
| Q4 | Activation du canal email (MVP-018) dès le MVP ou après | Produit | Phase 2 |
