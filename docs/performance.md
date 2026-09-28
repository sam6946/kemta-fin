# Performance — mesures et budget (phases 8 et 11)

Toutes les valeurs ci-dessous ont été **mesurées** ; les nombres de requêtes SQL sont **verrouillés
par des tests** qui échouent en cas de régression (`apps/dashboard/tests/test_dashboard_queries.py`,
`apps/core/tests/test_scalability.py`).

## 1. Requêtes SQL

| Endpoint | Requêtes SQL | Verrou |
|---|---|---|
| `GET /api/projects/{id}/dashboard/` (pilotage, projet complet) | **≤ 32** | test `ceiling` |
| idem, réponse servie par le cache | **≤ 4** (authentification, appartenance) | test cache HIT |
| idem, après écriture (jalon, preuve, dépense…) | recalcul immédiat | invalidation par version de projet (signaux) |
| `GET /api/workspace/` | **constant** quel que soit le nombre de projets | test avec 1 puis N projets |
| listes de preuves, jalons, tâches, dépenses | **constant** quelle que soit la taille de page | tests N+1 |
| `EvidenceSerializer` | pas de requête par ligne (auteur, revue, miniature préchargés) | corrigé en phase 11 |

Un dashboard est **une seule requête HTTP** côté frontend (pas 8 appels agrégés à la main).

## 2. Cache et interrogation

* Cache ciblé : réponse agrégée par (projet, utilisateur, jour), `DASHBOARD_CACHE_SECONDS` = 60.
  La clé contient un numéro de version du projet : toute écriture l'incrémente, l'utilisateur ne
  voit jamais un chiffre périmé après sa propre action. La date locale est dans la clé (les
  retards changent à minuit). Panne du cache → calcul direct, réponse identique.
* **Pas de polling** : la cloche de notifications se rafraîchit au montage, au changement de
  page, au retour d'onglet, au retour de connexion et après une action locale
  (`NOTIFICATIONS_CHANGED`). La synchronisation hors ligne planifie un seul réveil par
  échéance.
* Collections : pagination partout (défaut 20, maximum 100 via `page_size` ; les listes de
  planning/budget par projet : 100 par page, maximum 200) ; le dashboard borne ses listes (5
  dernières preuves/dépenses, 15 alertes, 10 événements d'activité) et l'espace de travail
  (20 projets, 20 tâches, 10 éléments par file de validation).

## 3. Médias

Listes → **vignette WebP 320 px** (≈ 5-15 Ko) générée en tâche de fond ; galerie → version 1080 px ;
original uniquement à l'ouverture. Sans vignette (tâche en retard), retour sur le fichier
d'origine. Le navigateur charge les vignettes en différé (`IntersectionObserver`) via `AuthImage`
(les `<img>` ne peuvent pas envoyer le jeton : la vignette est récupérée avec `Authorization`,
mise en `blob:` puis libérée au démontage).

## 4. Test de charge ciblé

Outil : `backend/loadtest/run.py` (bibliothèque standard, aucune dépendance). Il se connecte avec
4 rôles, enchaîne un parcours réaliste (espace de travail, dashboard ×2, preuves, jalons,
tâches, dépenses, notifications, file de validation), calcule p50/p95/max et **échoue** (code
de sortie 1) si un p95 dépasse le seuil (800 ms) ou si plus de 1 % des requêtes échouent.

```bash
cd backend
python manage.py migrate && python manage.py seed_dev
python manage.py seed_loadtest --evidences 1000 --tasks 400 --expenses 300   # volume réaliste
gunicorn config.wsgi:application --bind 127.0.0.1:8011 --workers 3 --threads 2 &
python loadtest/run.py --base-url http://127.0.0.1:8011 --users 8 --duration 20
```

### Résultat de référence (2026-09-29)

Environnement : bac à sable de développement, **SQLite**, cache local par processus, gunicorn 3
workers × 2 threads, 1 000 preuves + 400 tâches + 300 dépenses + 40 jalons sur le projet chargé,
8 utilisateurs simultanés, 20 s.

| Endpoint | n | moyenne | p50 | p95 | max |
|---|---|---|---|---|---|
| dashboard | 350 | 47,8 | 32,5 | **132** | 254 |
| workspace | 175 | 111,1 | 95,9 | **217** | 388 |
| evidences | 175 | 101,4 | 81,4 | **237** | 402 |
| evidences-pending | 168 | 90,2 | 64,4 | **238** | 340 |
| milestones | 175 | 110,3 | 93,5 | **213** | 358 |
| tasks | 174 | 177,1 | 161,2 | **319** | 427 |
| expenses | 172 | 135,8 | 117,5 | **285** | 432 |
| notifications | 169 | 60,2 | 41,3 | **166** | 323 |
| unread-count | 168 | 44,8 | 25,6 | **160** | 215 |

**1 726 requêtes, 86 req/s, 0 erreur.** Le dashboard (2 appels sur 2 → le second sert le cache)
est le plus rapide malgré le volume ; les listes non paginées à la main auraient dépassé la
seconde avec 1 000 preuves.

**Limites de cette mesure** (à ne pas sur-interpréter) : SQLite en fichier, client et serveur
sur la même machine, cache par processus (le taux de succès du cache est donc inférieur à celui
de Redis partagé). Les chiffres PostgreSQL + Redis + Nginx doivent être **refaits en
préproduction** avec la même commande ; le budget cible reste **p95 < 800 ms** par endpoint à
8 utilisateurs simultanés et **0 erreur**.

## 5. Budget de performance (frontend)

Build de production : bundle principal 285 kB (86,6 kB gzip) ; les pages `WorkspacePage`,
`ProjectActivity` et `NotificationsPage` sont chargées à la demande (`React.lazy`). Assets
fingerprintés servis avec `Cache-Control: immutable` (1 an), `index.html` en `no-cache`.

## 6. Limites connues

* Les écrans jalons/tâches/postes budgétaires/preuves en attente n'affichent que la **première
  page (100 éléments)** ; `next` est renvoyé par l'API. Un projet dépassant 100 éléments dans
  l'une de ces listes nécessitera un bouton « Charger la suite » (à planifier avant la
  première mise en production réelle).
* Le rapport de charge n'inclut pas l'envoi de fichiers (dépend du disque et du réseau du site).
