# Plan de tests — KEMTA SUIVI (Phase 0)

Objectif : chaque fonctionnalité ne passe « terminée » que si ses tests passent. Un test qui
n'existe pas équivaut à un critère d'acceptation non mesuré.

## 1. Pyramide

| Niveau | Outil | Couverture attendue |
|---|---|---|
| Unitaire (backend) | `pytest` + `pytest-django` | règles métier, services (OTP, normalisation téléphone, calculs financiers, avancement) |
| API / intégration | `DRF APIClient` + `pytest` | permissions, statuts HTTP, pagination, idempotence, N+1 |
| Unitaire (frontend) | `Vitest` + `Testing Library` | reducers, file de synchronisation, formatage, états UI |
| E2E | `Playwright` (Chromium + contexte mobile) | parcours critiques, offline/online |
| Performance | `pytest-django` `django_assert_num_queries`, `k6`/`Locust` (Phase 11) | N+1, latences |
| Sécurité | `pip-audit`, tests dédiés, revue | secrets, énumération, rate limiting |

Seuil de couverture backend : **85 %** sur `apps/`, blocage CI en dessous.

## 2. Commandes

```bash
# backend (depuis backend/)
pytest                       # tous les tests
pytest --cov=apps --cov-report=term-missing
pytest -m "not slow"

# frontend (depuis frontend/)
npm test                     # vitest
npm run test:e2e             # playwright (démarre le backend de test)

# tout (docker)
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec web pytest
```

## 3. Matrice de couverture (fonctionnalité → tests exigés)

| ID | Tests exigés |
|---|---|
| MVP-001 Inscription | numéro valide CM accepté · format invalide refusé · numéro déjà utilisé (409) · compte inactif avant OTP · pas d'email demandé · aucun secret dans les logs |
| MVP-002 OTP | expiration · code erroné · réutilisation · `purpose` incompatible · max tentatives · renvoi limité (numéro + IP) · stockage haché |
| MVP-003 Session | non confirmé refusé · token expiré · refresh rotation · refresh révoqué · logout blacklist · pas de boucle de retry |
| MVP-004 RBAC | **matrice paramétrée** : chaque permission critique × test positif + test négatif ; projet non autorisé → 404 ; action interdite → 403 |
| MVP-005 Org/Projets | création (org + projet) · rattachement obligatoire à une organisation · liste filtrée/paginée · non-membre → 404 · action interdite → 403 · dates incohérentes refusées · code projet unique par organisation · budget FCFA entier (centimes refusés) · dernier responsable protégé · changement de rôle journalisé (ancien/nouveau) · `CaptureQueriesContext` sur les listes (pas de N+1) |
| MVP-006 Jalons/Tâches | dates incohérentes refusées · avancement borné 0-100 · tâche terminée ⇒ 100 % et date réelle obligatoire · dépendance à soi-même/cycle ⇒ 409 · jalon et dépendance hors projet refusés · retards déterministes (tâche, jalon) · responsable désigné limité aux champs d'exécution · suppression logique journalisée · avancement projet, jalon et tâches sans jalon vérifiés par le calcul · `assert_num_queries` sur `/schedule/` |
| MVP-007 Preuve | upload valide (multipart) · `Idempotency-Key` manquant (400) · rejeu de la même clé → même preuve (`200`, en-tête `Idempotency-Replayed`) · doublon `(project, hash)` → 409 avec l'existante · fichier vide / non-image (magic bytes) / trop volumineux (413) / trop grand en pixels · `captured_at` futur refusé · GPS absent (`UNAVAILABLE`) · GPS refusé (`DENIED`) · GPS disponible : distance Haversine et refus hors périmètre (422) · hash SHA-256 réellement enregistré · chemin de stockage régénéré (nom client ignoré) · miniature et version liste générées · galerie filtrée par statut/auteur/tâche avec `counts` · accès aux fichiers contrôlé par appartenance (404 sinon) · frontend : compression mesurée avant/après, empreinte identique au serveur, position obtenue/refusée, envoi multipart sans `Content-Type` JSON |
| MVP-008 Validation | validateur autorisé · rôle non autorisé (403) · validation de sa propre preuve refusée (`cannot_validate_own_evidence`, sauf administrateur plateforme) · rejet et signalement avec commentaire obligatoire (400) · chaque transition autorisée et chaque transition interdite (409 + `allowed_actions`) · `REOPEN` ramène à `PENDING` · historique append-only (acteur, date, action, commentaire) · mise à jour/suppression d'une décision refusée même en admin · preuve rejetée toujours consultable · `ActivityLog` écrit à chaque décision · file d'attente du validateur (`/api/evidences/pending/`) sans preuves auto-validables |
| MVP-009 Offline | preuve persistée après rechargement (IndexedDB, binaire compris) · reprise après interruption (`UPLOADING` → nouvel envoi) · même clé d'idempotence réutilisée à chaque tentative · doublon évité (hash + clé, côté client **et** serveur) · backoff exponentiel borné + limite de 8 essais puis relance manuelle · conflits identifiés et jamais rejoués en boucle · erreurs visibles et relançables · retour online déclenche la synchro sans action · lot : opérations isolées, rejeu sans second effet, `op_in_progress`, `idempotency_key_conflict`, refus des lots ambigus (clés dupliquées, > 50) · rôles métier conservés hors ligne (responsable désigné ≠ planificateur) |
| MVP-010 Finances | **montants** : entiers FCFA (centimes refusés), montant ≤ 0 refusé · **totaux serveur** : `committed`/`paid`/`solde`/`taux` recalculés depuis le grand livre, tout total client ignoré (poste et dépense) · **budget** : somme des postes ≤ budget global (création et révision par écart), libellé unique par projet, poste porteur de dépenses non supprimable, suppression logique journalisée · **dépenses** : rattachement au projet et au poste du même projet, n° de facture unique par projet, machine à états complète (chaque transition autorisée **et** chaque transition interdite avec `allowed_actions`), brouillon non engageant, figées après approbation (`expense_locked`), rejet motivé puis resoumission · **paiements** : réservés à l'engagement, dépense approuvée exigée, `Σ paiements ≤ montant` (défense en profondeur après écriture), date non future, solde automatique en `PAID`, annulation par contre-écriture (`is_cancelled`, retour `APPROVED`), double annulation refusée · **dépassement** : refus `422` avec `overruns`, dépassement de poste inclus, exception motivée (≥ 10 caractères) journalisée `over_budget_override`, franchissement des seuils 80/100 % journalisé **une seule fois**, alertes déterministes (`BUDGET_EXCEEDED`, `BUDGET_THRESHOLD_REACHED`, `BUDGET_LINE_EXCEEDED`) · **ajustements** : motif obligatoire, sens débit/crédit, influence sur le solde et les seuils · **atomicité** : panne de journalisation en fin d'opération ⇒ aucune écriture partielle (approbation, paiement, annulation, ajustement, poste, mise à jour, justificatif) · **concurrence** : verrou `select_for_update` vérifié (SQL), invariant « total payé ≤ montant » après valeur périmée, deux paiements simultanés sur PostgreSQL (un seul passe) · **grand livre** : append-only (modèle, instance, queryset), `balance_after` exact, `CANCELLATION` de paiement qui ne libère pas l'engagement · **permissions** : lecture 403/404 selon périmètre, création (drapeau `manage_finance`), engagement refusé à un contractant même doté du drapeau, séparation des tâches · **justificatifs** : signature binaire (PDF/JPEG/PNG/WebP), `file_empty`, `file_required`, `413` au-delà de la limite, empreinte SHA-256, remplacement, accès privé · **N+1** : nombre de requêtes constant sur les listes de postes et de dépenses · **seed** : cohérence grand livre ↔ synthèse, six statuts présents, acteurs distincts et autorisés, idempotence, `--skip-finance` |
| **MVP-017 Reset mot de passe** | numéro inconnu (réponse neutre, pas d'énumération) · OTP invalide/expiré/réutilisé/autre `purpose` · max tentatives · renvoi limité · mot de passe faible · réutilisation du mot de passe courant · compte non confirmé · compte désactivé · **sessions révoquées après reset** · SMS de confirmation envoyé · journalisation des 5 événements · rate limiting · E2E « Mot de passe oublié ? » → reconnexion |
| MVP-011 Dashboard | endpoint agrégé · `assert_num_queries` ≤ seuil · cohérence avec les calculs backend · alertes déterministes · collections limitées · états loading/empty/error/offline |
| MVP-012 Journal | création d'événements pour chaque action sensible · protection contre suppression · accès réservé · pagination |
| MVP-013 Médias | compression avant/après (taille mesurée, côté appareil) · miniature WebP 320 px + version liste JPEG 1080 px générées côté serveur · limites taille/type établies sur le contenu réel · antivirus/quotas et relance de tâche échouée en phase 10 |
| MVP-014 Notifications | émission des 5 événements métier · regroupement · permissions de consultation |
| Outils de développement | `/api/dev/outbox/` disponible en dev, **404** hors développement, aucun secret exposé |
| MVP-015 Observabilité | `/health/` ok/dégradé (base ou Redis down) · erreurs structurées · **aucun secret ni OTP en clair dans les logs** |

## 4. Données de seed

Le seed financier passe par les **services** (jamais d'`INSERT` direct) : le grand livre, les
seuils et le journal d'activité restent la conséquence des règles métier, et les totaux de
démonstration correspondent exactement aux écritures.


- Commande `python manage.py seed_dev` : **explicitement identifiée comme données de
  développement**, refusée si `DJANGO_ENV=production` (sauf `--force`).
- Contexte camerounais : organisations de Douala/Yaoundé, projets en FCFA (montants réalistes :
  15 000 000 – 500 000 000 XAF), numéros `+2376XXXXXXXX`, jalons en français, preuves avec GPS
  autour de Douala.
- Comptes de démo : un utilisateur **par rôle** (9 rôles), mot de passe de développement
  documenté dans le README, OTP en mode console en dev.
- **Aucune donnée mockée dans le parcours produit final** : la seed sert aux tests et aux démos,
  jamais à simuler une fonctionnalité manquante.

## 5. E2E (MVP-016) — scénarios automatisés

1. inscription avec numéro → 2. validation OTP → 3. connexion → 4. ajout facultatif de l'email →
5. création d'organisation → 6. création de projet → 7. ajout de membre → 8. création de jalon →
9. capture de preuve → 10. validation de preuve → 11. création de dépense →
12. consultation du dashboard → 13. passage offline → 14. synchronisation automatique →
**15. réinitialisation du mot de passe après oubli**.

Exécution : `npm run test:e2e` dans un environnement documenté (backend de test + base SQLite +
SMS console). Le scénario 13/14 utilise `context.setOffline(true)` de Playwright, ferme l'onglet,
le rouvre, repasse en ligne et vérifie la synchronisation sans doublon.

## 6. Règles d'or

- Un test qui échoue de façon intermittente est un bug produit (blocage CI).
- Toute régression critique corrigée ajoute son test de non-régression.
- Les tests de permissions sont **générés depuis la matrice** (`docs/rbac-matrix.md`) pour que
  documentation et code ne puissent pas diverger silencieusement.
