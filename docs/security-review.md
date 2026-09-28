# Revue de sécurité — phase 11

Périmètre : API Django/DRF, frontend React, Nginx, Celery. Méthode : lecture du code contre la
liste de contrôle ci-dessous (inspirée OWASP ASVS niveau 1 + OWASP API Top 10), puis
**tests automatiques** pour chaque point critique (`backend/apps/core/tests/test_security.py`
et tests par application). Date de la revue : 2026-09-29.

## 1. Synthèse

| Zone | État | Preuve |
|---|---|---|
| Secrets hors dépôt | ✅ | `.env*` non versionné, `.env.example` sans valeur réelle ; test `test_no_secret_is_committed_in_the_repository` ; aucun secret dans les images (Nginx : certificats montés en lecture seule) |
| Authentification | ✅ | JWT courts (15 min) + refresh à rotation/révocation, verrouillage après 5 échecs, OTP hachés, message de réponse identique pour numéro inconnu |
| Limitation de débit OTP/auth | ✅ | portées `otp_request`, `password_reset`, `login`, `sensitive` + quotas par téléphone et par IP ; Nginx ajoute `limit_req` sur `/api/auth/` |
| Usurpation d'IP (`X-Forwarded-For`) | ✅ corrigée | voir §2 |
| Contrôle d'accès | ✅ | backend seul juge (RBAC 9 rôles + rôle par projet), hors périmètre → 404 ; matrice testée (`test_access_matrix.py`, `test_role_matrix.py`) |
| Fichiers envoyés | ✅ avec réserve | voir §3 |
| Médias privés | ✅ | pas de `/media/` public ; fichiers via `EvidenceFileView` (contrôle d'accès à chaque requête) puis `X-Accel-Redirect` |
| Transport | ✅ | HTTPS obligatoire, HSTS 1 an, cookies `Secure`, TLS ≥ 1.2, redirection 80 → 443 |
| Journal d'activité | ✅ | immuable dans l'application ; `REVOKE` PostgreSQL documenté (`docs/ops.md` §5) |
| Exploitation | ✅ | métriques par jeton, jamais exposées par Nginx ; aucune donnée personnelle dans les métriques ni dans les erreurs de tâches |
| Dépendances | 🟡 | versions figées ; à auditer en continu (`pip-audit`, `npm audit`) — voir §5 |

## 2. Limitation de débit et `X-Forwarded-For`

**Constat.** Un limiteur par IP est contournable si l'application croit l'en-tête
`X-Forwarded-For` fourni par le client : il suffit de changer l'en-tête à chaque requête.

**Correction.**
* `NUM_PROXIES` (env, défaut `0` ; `1` par défaut en production) : nombre de proxies de
  confiance. À 0, l'en-tête est ignoré et l'IP est `REMOTE_ADDR`. À 1, on lit **le dernier saut**
  (celui écrit par Nginx), jamais les valeurs à gauche, qui viennent du client.
* Nginx **écrase** l'en-tête (`proxy_set_header X-Forwarded-For $remote_addr`) au lieu de
  l'allonger : même un client malveillant ne peut rien injecter.
* Le même calcul (`get_client_ip`) sert aux limiteurs DRF, au quota OTP par IP, au verrouillage
  et au journal : une seule source de vérité. Tests : `test_security.py` (usurpation ignorée avec
  `NUM_PROXIES=0`, dernier saut avec 1).
* Les limiteurs **échouent ouverts** si Redis tombe (sinon toute l'API répondrait 500) ; la
  protection anti-force-brute du compte (verrouillage en base) reste active.

Quotas par défaut (variables `THROTTLE_*`) : login 10/min, OTP 20/h, réinitialisation 20/h,
actions sensibles 60/h, **envois de fichiers 240/h**, **synchronisation 600/h**, utilisateurs
authentifiés 2000/h, anonymes 60/min. Chaque refus (429) incrémente `kemta_rate_limited_total` (par route).

## 3. Fichiers : type, taille, contenu

| Flux | Types acceptés | Taille | Contrôle |
|---|---|---|---|
| Preuve terrain | JPEG, PNG, WebP | `MAX_UPLOAD_SIZE_MB` (10) ; 4000 px max | type **réel** détecté par Pillow (le `Content-Type` déclaré est ignoré), image décodée puis ré-encodée pour les dérivés (vignette 320 px WebP, version liste 1080 px) ; empreinte SHA-256 ; détection de doublon |
| Justificatif de dépense | JPEG, PNG, **PDF** | idem | signature d'octets (`\xff\xd8\xff`, PNG, `%PDF-`) |
| Nginx | — | `client_max_body_size 12m` | refuse avant Django |

**Réserve — PDF.** Un PDF n'est validé que par sa signature `%PDF-` : son contenu n'est ni
analysé, ni assaini (un PDF peut embarquer du JavaScript ou des liens). Atténuations en place :
le fichier n'est jamais servi depuis une origine publique, seulement par l'API authentifiée,
avec `X-Content-Type-Options: nosniff` et une CSP `default-src 'self'` ; il n'est pas rendu dans
l'application (téléchargé/ouvert par le visualiseur du navigateur). **Action recommandée avant
l'ouverture au public** : analyse antivirus (ClamAV) dans une tâche Celery et mise en quarantaine
(ADR à ouvrir), ou refus des PDF si les justificatifs photo suffisent.

## 4. Contrôle d'accès et journal

* Aucune permission n'est décidée côté frontend : les boutons reflètent `permissions` renvoyé
  par le backend, qui revérifie chaque écriture.
* Le tableau de bord n'expose les montants qu'aux rôles ayant `view_finance` (`budget` et
  `expenses` valent `null` sinon — test dédié pour l'agent terrain).
* Un utilisateur ne peut jamais valider ni rejeter **sa propre** preuve (403
  `cannot_validate_own_evidence`).
* Journal : adresse IP et appareil visibles seulement de l'administration ; les autres rôles
  voient acteur/action/entité/date. Aucune route d'écriture (405).
* Notifications : filtrées par utilisateur ; lire/marquer lue la notification d'un autre → 404.

## 5. Risques résiduels et suites

| # | Risque | Gravité | Décision |
|---|---|---|---|
| 1 | PDF non analysé (§3) | moyenne | antivirus avant l'ouverture publique |
| 2 | Limiteurs échouent ouverts si Redis tombe | faible | acceptable : verrouillage de compte en base ; alerte sur Redis |
| 3 | Jetons JWT dans `localStorage` (SPA) | moyenne | mitigé par CSP stricte (`script-src 'self'`), durée courte, rotation ; à réévaluer avec cookies `HttpOnly` |
| 4 | Pas de MFA au-delà de l'OTP d'inscription | faible (MVP) | ADR-007 (perte de SIM) avant production |
| 5 | Dépendances | variable | brancher `pip-audit` et `npm audit --omit=dev` dans la CI ; mises à jour mensuelles |
| 6 | `CSP 'unsafe-inline'` pour les styles | faible | remplacer par des noms de classes/hachages si les styles inline disparaissent |
| 7 | Sauvegardes non testées | élevée tant que non testées | restauration à blanc avant production (`docs/ops.md` §6) |

## 6. Liste de contrôle avant mise en production

- [ ] `python manage.py check --deploy` sans avertissement
- [ ] `SECRET_KEY`, mot de passe de base, `METRICS_TOKEN` générés aléatoirement
- [ ] `DEBUG=false`, `DJANGO_ENV=production`, `SMS_PROVIDER=real`
- [ ] Certificat TLS valide, test SSL Labs ≥ A
- [ ] `REVOKE` PostgreSQL du journal appliqués
- [ ] Comptes de démonstration absents (`seed_dev` jamais exécuté)
- [ ] Restauration de sauvegarde testée
