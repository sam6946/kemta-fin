# Flux financier — budget, dépenses, paiements (Phase 7 — MVP-010)

Ce document est la **règle métier de référence** du module financier. Le code qui l'applique vit
dans `backend/apps/finance/` : `models.py` (invariants), `access.py` (permissions),
`services.py` (**seul chemin d'écriture**), `serializers.py` (aucun total en entrée),
`views.py` (API) et `storage.py` (justificatifs).

## 1. Principes non négociables

1. **Montants en FCFA entiers.** Aucun centime n'est arrondi : il est **refusé**
   (`amount_has_cents`). Un total envoyé par le frontend est **ignoré** — les champs calculés
   sont en lecture seule côté sérialiseur.
2. **Tout est calculé côté serveur, en SQL, depuis le grand livre.** Le consommé, le payé, le
   solde, le taux de consommation et le « reste à payer » ne sont jamais transmis par un client,
   ni additionnés en Python.
3. **Le grand livre est la source de vérité** et il est *append-only* : aucune écriture n'est
   modifiée ni supprimée (ni par le modèle, ni par le queryset, ni par l'API, ni en admin).
   Une erreur se corrige par une **contre-écriture**, jamais par un `UPDATE`.
4. **Une opération financière est atomique** : `transaction.atomic()` + verrou
   `select_for_update()` sur la ligne du projet (et sur la dépense pour un paiement). Échec à
   n'importe quelle étape ⇒ aucune écriture partielle.
5. **Séparation des tâches** : celui qui crée une dépense ne l'approuve pas (sauf
   administrateur plateforme, pour l'exploitation).
6. **Tout est journalisé** : auteur, date, valeurs **avant/après** (montant, statut, poste),
   motif éventuel (`ActivityLog`, immuable).
7. **Aucune action financière en file hors ligne.** Le budget engage de l'argent réel : ces
   opérations exigent l'autorité du serveur. Hors connexion, l'interface l'annonce et propose
   de réessayer — les types d'opération de la file (phase 6) restent inchangés.

## 2. Vocabulaire et vocabulaire d'état

| Terme | Sens exact |
|---|---|
| **Prévu** (`planned`) | budget global du projet (`Project.budget_total`) |
| **Alloué** (`allocated`) | somme des postes budgétaires — ne peut pas dépasser le prévu |
| **Engagé** (`committed`) | dépenses approuvées (ou payées) + ajustements débiteurs − annulations de dépenses |
| **Payé** (`paid`) | paiements vivants − paiements annulés |
| **Reste à payer** (`outstanding`) | engagé − payé |
| **Solde** (`balance`) | prévu − engagé (négatif = dépassement) |
| **Taux de consommation** | `engagé × 100 / prévu`, borné à 2 décimales ; budget nul ⇒ 0 % ou 100 % |
| **Seuil** | `OK` (< 80 %), `WARNING` (≥ 80 %), `EXCEEDED` (≥ 100 %) |

## 3. Cycle de vie d'un poste budgétaire

`POST /api/projects/{id}/budget-lines/` → `BUDGET_LINE_CREATED`

- libellé **unique par projet** (`409 budget_line_already_exists`) ;
- montant **entier ≥ 0** (un poste à 0 documente une enveloppe à arbitrer) ;
- la **somme des postes ne peut pas dépasser le budget global** (`422 budget_lines_exceed_budget`,
  détails `allocated` / `planned` / `over`) — vérifié à la création **et** à chaque révision
  (on contrôle alors l'écart, pas le montant total) ;
- la modification journalise `BUDGET_LINE_UPDATED` avec l'ancienne et la nouvelle valeur ;
- un poste **portant des dépenses ne peut pas être supprimé** (`409 budget_line_in_use`) : sans
  cela, l'historique deviendrait illisible. Sinon la suppression est **logique**
  (`deleted_at`, `BUDGET_LINE_DELETED`).

## 4. Cycle de vie d'une dépense

```
DRAFT ──SUBMIT──▶ SUBMITTED ──APPROVE──▶ APPROVED ──PAID (soldée)──▶ PAID
  │                   │  │                  │        │
  │                   │  └──REJECT──▶ REJECTED        └──CANCEL──▶ CANCELLED
  └──CANCEL───────────┴────CANCEL──▶ CANCELLED
        REJECTED ──SUBMIT──▶ SUBMITTED          (correction puis resoumission)
```

| Transition | Qui | Effets |
|---|---|---|
| `SUBMIT` | capacité `manage_finance` | brouillon → soumise ; journal `EXPENSE_SUBMITTED` |
| `APPROVE` | rôle de pilotage (`can_settle_finance`) | **écriture au grand livre** `EXPENSE`/`DEBIT`, `approved_by`/`approved_at`, contrôle budgétaire, journal `EXPENSE_APPROVED` |
| `REJECT` | rôle de pilotage | **motif obligatoire** (`400 comment_required`) ; aucune écriture |
| `CANCEL` | rôle de pilotage | si la dépense était engagée : contre-écriture `CANCELLATION`/`CREDIT` qui **libère** l'engagement ; refusé si un paiement vivant existe (`409 expense_has_payments`) |

Règles complémentaires :

- une dépense est **toujours rattachée à un projet** ; le poste budgétaire, s'il est fourni,
  doit appartenir au **même projet** (`400 budget_line_other_project`) ;
- **numéro de facture unique par projet** (`409 invoice_already_used`) ;
- une dépense **n'est modifiable que tant que l'argent n'est pas engagé** : brouillon, soumise
  ou rejetée. Après approbation, le serveur refuse (`409 expense_locked`) : la correction passe
  par une annulation puis une nouvelle dépense ;
- `PAID` et `CANCELLED` sont **terminaux** : toute transition renvoie
  `409 invalid_transition` avec la liste des actions autorisées ;
- l'approbation d'une dépense créée par soi-même est refusée (`403 cannot_approve_own_expense`).

## 5. Paiements

- une dépense doit être **`APPROVED`** (ou déjà partiellement payée) pour être payée
  (`409 expense_not_approved`) ;
- la somme des paiements vivants ne peut **jamais** dépasser le montant de la dépense
  (`422 payment_exceeds_outstanding`, détails `outstanding`) ; la dépense passe automatiquement à
  `PAID` quand le reste dû atteint zéro ;
- date de paiement non future (`400 payment_date_in_future`), moyens
  `CASH` / `BANK_TRANSFER` / `MOBILE_MONEY` / `CHEQUE` ;
- **annuler un paiement ne le supprime pas** : contre-écriture `CANCELLATION`/`CREDIT` +
  `cancelled_at`/`cancelled_by`, et la dépense redevient `APPROVED`. Un paiement déjà annulé
  renvoie `409 payment_already_cancelled` ;
- chaque écriture porte le **solde après opération** (`balance_after`) : l'historique se relit
  sans recalcul.

## 6. Dépassements de budget

1. L'engagement est **refusé** (`422 budget_exceeded`) quand il ferait dépasser le budget global
   **ou** un poste budgétaire, avec le détail `overruns` (poste, prévu, engagé après, dépassement)
   et la longueur minimale du motif.
2. Le dépassement peut être **assumé** en fournissant `override_reason` (≥ 10 caractères). Le
   motif est journalisé (`over_budget_override`) et l'alerte reste visible dans la synthèse :
   un dépassement n'est jamais silencieux.
3. Le **franchissement** des seuils 80 % et 100 % est journalisé **une seule fois**
   (`BUDGET_THRESHOLD_REACHED`, `BUDGET_EXCEEDED`) — pas à chaque écriture au-dessus du seuil.
4. Les alertes sont **déterministes** et servies par l'API : `BUDGET_THRESHOLD_REACHED`,
   `BUDGET_EXCEEDED`, `BUDGET_LINE_EXCEEDED` (poste dépassé, même si le budget global tient).

## 7. Ajustements

`POST /api/projects/{id}/adjustments/` — réservé au pilotage financier.

- **motif obligatoire** (≥ 5 caractères) ;
- sens explicite : `DEBIT` (consomme le budget) ou `CREDIT` (libère le budget) ;
- écrit au grand livre (`ADJUSTMENT`), recalcule le solde, journalise `ADJUSTMENT_RECORDED`,
  et peut franchir un seuil (journalisation du franchissement).

## 8. Justificatifs (factures)

- formats acceptés : JPEG, PNG, WebP, PDF — **déterminés par la signature binaire**, jamais par
  l'extension ni par le type déclaré (`415 unsupported_media_type`) ;
- taille limitée par `MAX_UPLOAD_SIZE_MB` (`413 file_too_large`), fichier vide refusé ;
- nom de fichier **régénéré par le serveur** ; empreinte **SHA-256** enregistrée
  (`receipt_hash`) pour la traçabilité ;
- lecture et dépôt sont contrôlés par les permissions financières du projet (dépôt =
  `manage_finance`) ; le fichier est servi avec `Cache-Control: private` ;
- le remplacement d'un justificatif est journalisé (`EXPENSE_RECEIPT_ATTACHED`).

## 9. Permissions (détail de `docs/rbac-matrix.md` §6)

| Niveau | Qui | Ce que cela permet |
|---|---|---|
| `view_finance` | PLATFORM_ADMIN, ORG_OWNER, PROJECT_OWNER, ENGINEER, CONTRACTOR, FINANCE, INVESTOR | lire budget, dépenses, paiements et grand livre |
| `manage_finance` | les rôles ci-dessus + tout membre doté du drapeau `can_manage_finance` (ex. CONTRACTOR) | créer une dépense, la corriger, la soumettre, déposer un justificatif |
| **engagement** (`can_settle_finance`) | PLATFORM_ADMIN, ORG_OWNER, PROJECT_OWNER, FINANCE (et **pas** un simple drapeau `can_manage_finance`) | approuver, rejeter, annuler, payer, tenir le budget, ajuster |

**Décision produit (phase 7)** : le drapeau `can_manage_finance` accorde la **gestion**
(préparer la dépense) mais jamais l'**engagement**. Un contractant peut saisir ses factures ; il
ne peut ni approuver, ni payer, ni redistribuer le budget. Un projet hors périmètre répond **404**
(jamais 403) : son existence n'est pas révélée.

## 10. Concurrence

Le verrou `select_for_update()` porte sur la ligne **projet** : toutes les écritures d'un chantier
sont sérialisées, donc deux paiements simultanés ne peuvent pas dépasser le montant dû. En
défense en profondeur, le total payé est **revérifié après l'écriture** du paiement : en cas
d'incohérence (valeur périmée lue juste avant), l'opération entière est annulée — un solde
incohérent ne peut jamais être persisté.

## 11. Événements journalisés (immuables)

`BUDGET_LINE_CREATED`, `BUDGET_LINE_UPDATED`, `BUDGET_LINE_DELETED`, `EXPENSE_CREATED`,
`EXPENSE_UPDATED`, `EXPENSE_SUBMITTED`, `EXPENSE_APPROVED`, `EXPENSE_REJECTED`,
`EXPENSE_CANCELLED`, `EXPENSE_RECEIPT_ATTACHED`, `PAYMENT_RECORDED`, `PAYMENT_CANCELLED`,
`ADJUSTMENT_RECORDED`, `BUDGET_THRESHOLD_REACHED`, `BUDGET_EXCEEDED`.

Chaque événement porte l'auteur, la date, le projet et les valeurs avant/après utiles.

## 12. Ce qui n'est pas dans la phase 7

- le **dashboard agrégé** `/api/projects/{id}/dashboard/` et l'écran de synthèse multi-projets
  (phase 8) ; l'endpoint `/api/projects/{id}/finance/` sert déjà de source unique ;
- l'**écran d'activité** (phase 9) : les événements financiers y apparaîtront sans changement ;
- les **notifications** d'approbation (phase 10) ;
- l'**export comptable** (hors périmètre MVP), les devises autres que le FCFA et la
  comptabilité en partie double (le grand livre interne est volontairement simple).
