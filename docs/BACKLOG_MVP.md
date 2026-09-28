# KEMTA SUIVI — Backlog MVP priorisé

> Document de référence du périmètre MVP. Toute fonctionnalité décrite ici n'est « terminée »
> que si elle satisfait sa **définition de terminé (DoD)**, ses **critères d'acceptation
> mesurables**, ses **dépendances** et les **exigences transverses** (sécurité, tests,
> documentation, observabilité).
>
> **Ajouts par rapport au cadrage initial (repérés par 🆕)** : la réinitialisation du mot de
> passe a été intégrée comme fonctionnalité bloquante **MVP-017 (P0)**, car l'authentification
> par téléphone + mot de passe n'est pas exploitable sans mécanisme de récupération de compte.

## Sommaire

1. [Règles de priorisation](#1-règles-de-priorisation)
2. [Phases et livrables](#2-phases-et-livrables) (Phase 0 → Phase 11)
3. [Backlog priorisé des fonctionnalités MVP](#3-backlog-priorisé-des-fonctionnalités-mvp)
   - [P0 — Bloquant pour le MVP](#p0--bloquant-pour-le-mvp) : MVP-001 → MVP-012, **MVP-017 🆕**, MVP-018 🆕
   - [P1 — Nécessaire avant mise en production](#p1--nécessaire-avant-mise-en-production) : MVP-013 → MVP-016
4. [Dépendances critiques](#4-dépendances-critiques)
5. [Définition de terminé commune](#5-définition-de-terminé-commune-à-toutes-les-fonctionnalités)
6. [Hors périmètre du MVP](#6-hors-périmètre-du-mvp)
7. [Validation finale du MVP](#7-validation-finale-du-mvp)
8. [Checklist de passage de phase](#8-checklist-de-passage-de-phase)

---

## 1. Règles de priorisation

Les fonctionnalités sont classées selon l'ordre suivant :

1. fiabilité métier et intégrité des données ;
2. simplicité d'utilisation ;
3. fonctionnement sur réseau dégradé ;
4. sécurité et contrôle d'accès ;
5. performance ;
6. évolutivité ;
7. intelligence artificielle, hors MVP.

Une fonctionnalité ne peut passer à l'état « terminée » que si elle respecte sa définition de
terminé, ses critères d'acceptation mesurables, ses dépendances et les exigences transverses de
sécurité, tests, documentation et observabilité.

---

## 2. Phases et livrables

### Phase 0 — Cadrage et conception

**Objectif :** stabiliser les décisions structurantes avant le développement.

**Livrables :**

- modèle de données validé ;
- matrice des rôles et permissions ;
- flux d'inscription, OTP et connexion ;
- **flux de réinitialisation du mot de passe 🆕** ;
- flux de création et de suivi d'un projet ;
- flux de capture et de synchronisation d'une preuve ;
- flux financiers ;
- contrat API ;
- stratégie offline-first ;
- stratégie de cache ;
- architecture Docker ;
- stratégie de stockage des médias ;
- plan de tests ;
- backlog détaillé (ce document) ;
- documentation d'architecture initiale.

**Critères de sortie :**

- aucun modèle principal sans responsabilité définie ;
- chaque endpoint MVP possède un consommateur frontend identifié ;
- chaque rôle possède des permissions explicites ;
- les conflits de synchronisation et les opérations idempotentes sont spécifiés ;
- les décisions non résolues sont documentées avec un responsable et une date de résolution.

### Phase 1 — Fondations techniques

**Objectif :** fournir une base exécutable, testable et déployable.

**Livrables :** dépôt frontend React/TypeScript/Vite ; dépôt ou application backend
Django/DRF ; PostgreSQL ; Redis ; Celery worker et beat ; Docker Compose développement ;
configuration production initiale ; `.env.example` ; migrations initiales ; endpoint `/health/` ;
logs structurés ; pipeline de tests ; README d'installation.

**Critères de sortie :**

- tous les services démarrent avec Docker Compose ;
- `/health/` vérifie au minimum l'application, PostgreSQL et Redis ;
- aucune clé secrète n'est présente dans le dépôt ;
- les migrations s'exécutent sur une base vide ;
- les tests backend et frontend s'exécutent avec une commande documentée ;
- les erreurs critiques sont visibles dans les logs structurés.

### Phase 2 — Authentification et RBAC

**Objectif :** sécuriser l'accès à la plateforme avec un numéro de téléphone comme identifiant
principal.

**Livrables :**

- inscription par téléphone ;
- OTP SMS ;
- connexion ;
- refresh token ;
- gestion de session ;
- ajout facultatif de l'email ;
- **réinitialisation du mot de passe 🆕** ;
- rôles et permissions ;
- rate limiting ;
- journalisation des événements sensibles ;
- écrans d'authentification ;
- tests unitaires, API et E2E.

**Critères de sortie :**

- l'inscription est impossible sans numéro de téléphone valide ;
- l'inscription ne demande pas d'adresse email ;
- le numéro est normalisé et unique côté backend ;
- l'OTP est stocké uniquement sous forme hachée ;
- un OTP expiré est refusé ;
- le nombre de tentatives et de renvois est limité ;
- **un utilisateur ayant oublié son mot de passe peut le réinitialiser via un OTP reçu par SMS,
  sans intervention du support 🆕** ;
- **la réinitialisation révoque les sessions actives et est journalisée 🆕** ;
- les endpoints sensibles refusent toute requête non autorisée ;
- le frontend adapte l'interface aux permissions, mais le backend reste l'autorité ;
- le parcours complet inscription → OTP → connexion fonctionne sur mobile ;
- les tests couvrent les numéros déjà utilisés, OTP invalide, OTP expiré, dépassement de
  tentatives, renvoi contrôlé **et réinitialisation de mot de passe 🆕**.

### Phase 3 — Organisations, projets et membres

**Objectif :** permettre de créer un espace de travail et de contrôler l'accès aux projets.

**Livrables :** organisations ; projets ; membres de projet ; rôles par projet ; invitations ou
ajout de membres ; liste et détail des projets ; contrôle d'accès par organisation et projet ;
endpoints spécialisés ; écrans responsive ; tests de permissions.

**Critères de sortie :**

- un utilisateur autorisé peut créer une organisation ;
- un utilisateur autorisé peut créer un projet avec nom, localisation, devise FCFA et statut ;
- un projet est toujours rattaché à une organisation ;
- un membre ne peut accéder qu'aux projets autorisés ;
- un utilisateur sans permission reçoit une réponse HTTP 403 ;
- les changements de rôle sont journalisés ;
- les listes de projets sont paginées ;
- les requêtes principales ne génèrent pas de N+1 détectable par les tests ou l'analyse SQL.

### Phase 4 — Jalons, tâches et planning simplifié

**Objectif :** suivre l'avancement opérationnel du chantier.

**Livrables :** jalons ; tâches ; dates prévues et réelles ; statuts ; pourcentage d'avancement ;
dépendances simples entre tâches ; vue liste ; Gantt simplifié ; calcul d'avancement projet ;
alertes de retard déterministes.

**Critères de sortie :**

- un jalon possède au minimum un titre, un statut, une date prévue et un projet ;
- une tâche peut être rattachée à un jalon ;
- les statuts disponibles sont documentés et validés côté backend ;
- l'avancement global est calculé côté serveur ;
- une tâche en retard est détectée lorsque sa date prévue est dépassée et qu'elle n'est pas
  terminée ;
- les dates incohérentes sont refusées ;
- les modifications importantes sont journalisées ;
- la vue planning reste utilisable sur mobile ;
- les tests couvrent création, modification, clôture, retard et permissions.

### Phase 5 — Preuves terrain

**Objectif :** capturer des preuves contextualisées et vérifiables depuis un téléphone.

**Livrables :** capture ou sélection contrôlée d'une photo ; compression locale ; GPS ;
timestamp ; informations appareil ; description ; rattachement au projet et à l'utilisateur ;
hash du fichier ; statut de synchronisation ; validation, rejet et signalement ; historique des
validations ; galerie paginée ; affichage des statuts.

**Critères de sortie :**

- une preuve contient un `project_id`, un `user_id`, un timestamp et un statut ;
- le GPS est demandé avec gestion explicite du refus ou de l'indisponibilité ;
- le serveur valide les métadonnées reçues ;
- le hash du fichier est calculé et enregistré ;
- une preuve ne peut pas être validée par un utilisateur sans permission ;
- les statuts `PENDING`, `VALIDATED`, `REJECTED` et `FLAGGED` sont appliqués côté backend ;
- l'interface affiche toujours le statut et l'historique de validation ;
- les images sont compressées avant upload ;
- les thumbnails sont utilisés dans les listes ;
- les tests couvrent upload valide, fichier invalide, absence de GPS, doublon, validation et
  rejet.

### Phase 6 — Offline-first et synchronisation

**Objectif :** permettre la continuité du travail sans connexion fiable.

**Livrables :** IndexedDB ; stockage local des données structurées ; stockage temporaire des
médias ; file de synchronisation ; statuts `PENDING`, `UPLOADING`, `SYNCED`, `FAILED`,
`CONFLICT` ; retry exponentiel ; limite de retries ; reprise après interruption ; synchronisation
au retour en ligne ; idempotency keys ; déduplication ; interface de suivi de synchronisation ;
stratégie de résolution des conflits.

**Critères de sortie :**

- une preuve peut être créée sans connexion ;
- la preuve locale conserve ses métadonnées et son fichier ;
- l'utilisateur voit clairement les éléments en attente, synchronisés ou en erreur ;
- la synchronisation démarre automatiquement au retour en ligne ;
- une interruption réseau ne crée pas de doublon ;
- une même idempotency key produit un seul résultat serveur ;
- les retries utilisent un délai croissant et s'arrêtent après la limite configurée ;
- les conflits sont identifiés et présentés à l'utilisateur ou traités par une règle documentée ;
- le parcours E2E couvre passage offline, création, fermeture de l'application, retour online et
  synchronisation ;
- aucune action terrain critique ne dépend d'une requête immédiate.

### Phase 7 — Budget, dépenses et transactions

**Objectif :** fournir un suivi financier fiable et auditable.

**Livrables :** budget projet ; postes budgétaires ; dépenses ; factures ; paiements ;
transactions financières ; calcul du consommé et du solde ; historique des modifications ;
permissions financières ; transactions PostgreSQL atomiques ; tests de cohérence.

**Critères de sortie :**

- tous les montants sont stockés avec une précision adaptée au FCFA ;
- les totaux sont calculés côté serveur ;
- les totaux envoyés par le frontend sont ignorés ou vérifiés ;
- une opération financière critique utilise `transaction.atomic()` ;
- une dépense ne peut pas être rattachée à un projet inaccessible ;
- les modifications financières sont journalisées avec auteur, date et ancienne/nouvelle valeur ;
- le budget consommé et le solde sont cohérents après création, modification et annulation ;
- les opérations concurrentes ne produisent pas de solde incohérent ;
- les tests couvrent permissions, arrondis, dépassement, rollback et concurrence représentative.

### Phase 8 — Dashboard projet et workspace métier

**Objectif :** rendre l'état du chantier compréhensible en quelques secondes.

**Livrables :** endpoint agrégé `/api/projects/:id/dashboard/` ; avancement global ; budget
prévu ; dépenses ; solde ; dernier et prochain jalon ; alertes ; dernières preuves ; dernières
dépenses ; activité récente ; permissions ; dashboard investisseur ; workspace ingénieur/PME ;
cache ciblé ; états loading, empty, error et offline.

**Critères de sortie :**

- une vue principale est alimentée par un endpoint agrégé ;
- le dashboard affiche les indicateurs essentiels sans requêtes répétées inutiles ;
- les données financières affichées correspondent aux calculs backend ;
- les alertes de retard et de dépassement sont déterministes ;
- les collections sont paginées ou limitées ;
- le dashboard est utilisable sur mobile ;
- le temps de chargement et le nombre de requêtes sont mesurés sur un environnement de référence ;
- aucune boucle de polling de cinq secondes n'est utilisée ;
- les permissions affichées correspondent aux permissions backend.

### Phase 9 — Journalisation, validations et suppression logique

**Objectif :** rendre les actions sensibles traçables.

**Livrables :** `ActivityLog` ; journalisation des événements d'authentification ; journalisation
des changements de rôle ; journalisation des preuves ; journalisation des validations et rejets ;
journalisation financière ; suppression logique ; consultation paginée de l'activité ;
métadonnées IP/appareil lorsque légalement et techniquement approprié.

**Critères de sortie :**

- chaque action sensible définie dans le périmètre produit crée un événement ;
- les événements critiques ne sont pas supprimés physiquement ;
- un événement contient au minimum acteur, action, entité, identifiant, date et projet lorsque
  pertinent ;
- les journaux sont accessibles uniquement aux rôles autorisés ;
- les suppressions logiques excluent les éléments des vues normales sans effacer l'historique
  critique ;
- les tests vérifient la création et la protection des journaux.

### Phase 10 — Traitements asynchrones et notifications internes

**Objectif :** déplacer les traitements non critiques hors des requêtes HTTP.

**Livrables :** Celery ; Redis broker/cache ; traitement asynchrone des médias ; notifications
in-app ; événements métier ; regroupement des notifications ; suivi des tâches ; nettoyage des
fichiers temporaires ; documentation d'exploitation.

**Critères de sortie :**

- les traitements lourds ne bloquent pas les requêtes HTTP principales ;
- une tâche échouée est journalisée avec son erreur ;
- les tâches critiques disposent d'une stratégie de retry ;
- les événements `MilestoneValidated`, `ExpenseSubmitted`, `EvidenceRejected`,
  `BudgetThresholdReached` et `ProjectDelayed` sont émis lorsque leurs conditions sont remplies ;
- les notifications sont regroupées lorsque plusieurs événements similaires surviennent ;
- les files Celery et Redis sont observables ;
- les tests couvrent succès, échec et retry.

### Phase 11 — Performance, sécurité et observabilité

**Objectif :** rendre le MVP exploitable en production.

**Livrables :** pagination complète ; optimisation SQL ; cache Redis ciblé ; compression HTTP ;
lazy loading ; code splitting ; images responsive ; métriques API ; suivi des erreurs ; suivi des
uploads ; suivi des synchronisations ; rate limiting ; validation des fichiers ; configuration
HTTPS production ; revue de sécurité ; tests de charge ciblés.

**Critères de sortie :**

- aucune requête N+1 identifiée sur les parcours principaux ;
- les collections volumineuses sont paginées ;
- les images de liste utilisent des thumbnails ;
- les temps de réponse API et erreurs sont mesurés ;
- les erreurs de synchronisation sont comptabilisées ;
- les endpoints OTP et authentification sont protégés par rate limiting ;
- les fichiers sont contrôlés par type et taille ;
- les secrets sont fournis uniquement par variables d'environnement ;
- les résultats des tests de performance sont documentés ;
- les régressions critiques sont bloquées par les tests automatisés.

---

## 3. Backlog priorisé des fonctionnalités MVP

### P0 — Bloquant pour le MVP

#### MVP-001 — Inscription par numéro de téléphone

**Dépendances :** Phase 0 ; Phase 1 ; modèle utilisateur ; fournisseur SMS ou adaptateur mocké
uniquement pour les tests.

**Livrables :** modèle utilisateur ; normalisation du numéro ; endpoint d'inscription ; écran
d'inscription ; gestion des erreurs ; tests.

**Critères d'acceptation :**

- le numéro est obligatoire ;
- l'email n'est pas demandé ;
- les formats invalides sont refusés ;
- les numéros camerounais applicables sont acceptés après normalisation ;
- un numéro déjà utilisé est refusé avec un message non ambigu ;
- aucun compte n'est actif avant validation OTP ;
- les données sensibles ne sont pas exposées dans les logs.

**Définition de terminé :** code backend et frontend livré ; tests unitaires, API et E2E
passants ; permissions vérifiées ; documentation du flux disponible ; aucun secret fournisseur
SMS dans le code ; revue de sécurité effectuée.

#### MVP-002 — OTP SMS sécurisé

**Dépendances :** MVP-001 ; configuration fournisseur SMS ; rate limiting.

**Livrables :** génération OTP ; stockage haché ; expiration ; validation ; renvoi contrôlé ;
limitation des tentatives ; journalisation.

**Critères d'acceptation :**

- un OTP expire après une durée configurée ;
- un OTP incorrect est refusé ;
- le nombre maximal de tentatives est appliqué ;
- le renvoi est limité par numéro et adresse réseau selon la stratégie définie ;
- un OTP déjà consommé ne peut pas être réutilisé ;
- les messages ne révèlent pas d'informations sensibles ;
- les tests couvrent expiration, réutilisation, brute force et renvoi.

**Définition de terminé :** flux réel ou adaptateur fournisseur validé ; tests de sécurité
passants ; métriques d'échec disponibles ; documentation opérationnelle du fournisseur SMS ;
parcours mobile validé.

#### MVP-003 — Connexion et sessions

**Dépendances :** MVP-002 ; JWT ; RBAC.

**Livrables :** connexion ; access token ; refresh token ; expiration ; rotation lorsque
configurée ; déconnexion ; gestion des sessions frontend.

**Critères d'acceptation :**

- un utilisateur non confirmé ne peut pas se connecter ;
- un token expiré est refusé ;
- un refresh valide permet de renouveler la session ;
- une session révoquée ne peut plus être utilisée ;
- les erreurs sont gérées sans boucle de retry infinie.

**Définition de terminé :** tests backend et frontend passants ; stockage des tokens conforme à
la stratégie de sécurité ; documentation du cycle de session ; logs de sécurité disponibles.

#### MVP-004 — RBAC et permissions backend

**Dépendances :** MVP-003 ; matrice des rôles.

**Livrables :** rôles initiaux ; permissions globales et par projet ; contrôles DRF ; adaptation
frontend ; tests de matrice.

**Critères d'acceptation :**

- les neuf rôles initiaux sont disponibles ;
- chaque endpoint sensible vérifie les permissions côté backend ;
- un utilisateur ne peut pas accéder à un projet non autorisé ;
- un utilisateur ne peut pas modifier une donnée sans permission ;
- le frontend masque ou désactive les actions non autorisées sans remplacer le contrôle backend ;
- chaque permission critique possède au moins un test positif et un test négatif.

**Définition de terminé :** matrice publiée ; tests de permissions passants ; revue de sécurité
effectuée ; documentation des règles d'accès disponible.

#### MVP-005 — Organisations, projets et membres

**Dépendances :** MVP-004 ; Phase 3.

**Livrables :** CRUD organisation ; CRUD projet ; membres ; rôles par projet ; liste et détail ;
invitations ou ajout contrôlé.

**Critères d'acceptation :**

- un projet appartient à une organisation ;
- un membre ne voit que les projets autorisés ;
- les rôles par projet sont appliqués ;
- les suppressions sont contrôlées ;
- les listes sont paginées ;
- les changements de membres sont journalisés.

**Définition de terminé :** API, UX et tests livrés ; données de seed réalistes en FCFA ;
documentation API disponible ; absence de N+1 vérifiée.

#### MVP-006 — Jalons, tâches et avancement

**Dépendances :** MVP-005.

**Livrables :** modèles ; endpoints ; écrans liste et détail ; planning simplifié ; calcul
d'avancement ; alertes de retard.

**Critères d'acceptation :**

- les dates et statuts sont validés côté backend ;
- l'avancement global est calculé côté serveur ;
- les tâches en retard sont identifiées ;
- les utilisateurs autorisés peuvent créer et modifier ;
- les utilisateurs non autorisés sont bloqués ;
- le planning est utilisable sur mobile.

**Définition de terminé :** tests métier et E2E passants ; calculs vérifiés sur données de seed ;
documentation des règles d'avancement ; journalisation active.

#### MVP-007 — Capture de preuve terrain

**Dépendances :** MVP-005 ; stockage média ; permissions appareil ; Phase 5.

**Livrables :** capture photo ; compression ; GPS ; timestamp ; description ; hash ;
rattachement projet/utilisateur ; statut initial.

**Critères d'acceptation :**

- une preuve peut être créée depuis un téléphone ;
- la capture fonctionne sans connexion ;
- le GPS et le timestamp sont enregistrés lorsqu'ils sont disponibles ;
- le refus GPS est géré sans blocage silencieux ;
- le fichier est compressé avant upload ;
- le serveur vérifie les métadonnées ;
- le statut initial est visible.

**Définition de terminé :** parcours terrain validé sur appareil mobile réel ; tests offline et
upload passants ; validation des fichiers active ; thumbnails générés ; documentation du flux
disponible.

#### MVP-008 — Validation et historique des preuves

**Dépendances :** MVP-007 ; MVP-004 ; journalisation.

**Livrables :** validation ; rejet ; signalement ; historique ; affichage du statut ; permissions
multi-acteurs.

**Critères d'acceptation :**

- seuls les rôles autorisés peuvent valider ou rejeter ;
- chaque changement de statut est journalisé ;
- l'historique affiche acteur, date, action et commentaire lorsque requis ;
- une preuve rejetée reste consultable avec son statut ;
- une preuve n'est jamais présentée comme absolue sans statut.

**Définition de terminé :** tests de workflow passants ; historique protégé ; UX validée sur
mobile ; documentation des transitions d'état disponible.

#### MVP-009 — File offline et synchronisation idempotente

**Dépendances :** MVP-007 ; IndexedDB ; API de synchronisation ; idempotency keys.

**Livrables :** file locale ; statuts ; retry ; reprise ; déduplication ; conflits ;
synchronisation automatique ; écran de suivi.

**Critères d'acceptation :**

- une preuve créée offline est conservée après rechargement ;
- la synchronisation reprend après interruption ;
- un même fichier n'est pas envoyé deux fois ;
- une même opération répétée ne crée pas de doublon ;
- les erreurs sont visibles et relançables ;
- les conflits sont identifiés ;
- le retour online déclenche la synchronisation sans action obligatoire de l'utilisateur.

**Définition de terminé :** E2E offline/online passant ; tests d'idempotence passants ; stratégie
de conflit documentée ; métriques d'échec disponibles ; aucune perte de preuve dans les scénarios
testés.

#### MVP-010 — Budget et dépenses

**Dépendances :** MVP-005 ; permissions financières ; PostgreSQL transactionnel.

**Livrables :** budget ; postes ; dépenses ; paiements ; transactions ; calculs ; historique.

**Critères d'acceptation :**

- les montants sont exprimés en FCFA ;
- les totaux sont calculés côté serveur ;
- les opérations critiques sont atomiques ;
- les dépenses sont rattachées à un projet ;
- les dépassements sont détectables ;
- les modifications sont journalisées ;
- les permissions financières sont appliquées.

**Définition de terminé :** tests de calcul et rollback passants ; tests de permissions passants ;
données de seed cohérentes ; documentation des règles financières disponible.

#### MVP-011 — Dashboard projet agrégé

**Dépendances :** MVP-006 ; MVP-008 ; MVP-010 ; activité ; endpoint agrégé.

**Livrables :** dashboard investisseur ; workspace ingénieur/PME ; endpoint agrégé ; indicateurs ;
alertes ; dernières activités ; permissions ; cache ciblé.

**Critères d'acceptation :**

- l'utilisateur comprend l'état du projet en quelques secondes ;
- l'avancement, budget, dépenses et solde sont cohérents ;
- le dernier et prochain jalon sont visibles ;
- les alertes sont explicables ;
- les preuves et dépenses récentes sont accessibles ;
- les données principales sont chargées avec un nombre de requêtes mesuré et documenté ;
- les états loading, empty, error et offline sont présents.

**Définition de terminé :** tests E2E investisseur et ingénieur passants ; performance mesurée ;
absence de N+1 vérifiée ; responsive mobile validé ; documentation de l'endpoint disponible.

#### MVP-012 — Journal d'activité

**Dépendances :** MVP-004 ; MVP-005 ; MVP-008 ; MVP-010.

**Livrables :** modèle `ActivityLog` ; événements sensibles ; consultation paginée ; protection
contre suppression physique ; métadonnées utiles.

**Critères d'acceptation :**

- les créations, modifications, validations, rejets, paiements, changements de rôle, preuves et
  événements d'authentification sont journalisés ;
- les événements critiques sont immuables ou protégés ;
- les journaux sont accessibles uniquement aux rôles autorisés ;
- les événements contiennent acteur, action, entité et date ;
- la timeline est paginée.

**Définition de terminé :** couverture des événements définis ; tests de journalisation passants ;
revue de sécurité effectuée ; documentation disponible.

#### MVP-017 — Réinitialisation du mot de passe (mot de passe oublié) 🆕

> **Pourquoi P0 :** l'identifiant principal est le numéro de téléphone et l'accès se fait par
> téléphone + mot de passe. Sans mécanisme de récupération, tout oubli de mot de passe bloque
> définitivement l'utilisateur et nécessite une intervention manuelle du support : c'est une
> régression de fiabilité métier (règle de priorisation n°1) et un risque d'exploitation.
> Le canal de récupération est donc le **même canal que l'inscription : l'OTP SMS**, ce qui
> évite d'ajouter une dépendance à l'email (qui reste facultatif dans le MVP).

**Dépendances :** MVP-001 (modèle utilisateur + normalisation du numéro) ; MVP-002 (OTP haché,
expiré, limité) ; MVP-003 (JWT, révocation de session) ; MVP-012 (journalisation) ; rate limiting
; fournisseur SMS ou adaptateur mocké en test.

**Livrables :**

- endpoint de demande de réinitialisation (`POST /api/auth/password/reset/request/`) ;
- endpoint de confirmation (`POST /api/auth/password/reset/confirm/`) ;
- endpoint de changement de mot de passe pour utilisateur connecté
  (`POST /api/auth/password/change/`, ancien mot de passe obligatoire) ;
- OTP de réinitialisation à usage unique, distinct dans son `purpose` des OTP d'inscription ;
- révocation de toutes les sessions actives après réinitialisation réussie ;
- notification SMS de confirmation (« votre mot de passe vient d'être modifié ») ;
- politique de mot de passe validée côté serveur (longueur, non-réutilisation du mot de passe
  courant, mots de passe courants interdits) ;
- écrans frontend « Mot de passe oublié ? » (saisie du téléphone → OTP → nouveau mot de passe →
  confirmation) ;
- journalisation des événements `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET_CONFIRMED`,
  `PASSWORD_RESET_FAILED`, `PASSWORD_CHANGED` ;
- métriques (demandes, succès, échecs, OTP expirés) ;
- tests unitaires, API et E2E.

**Critères d'acceptation :**

- un lien « Mot de passe oublié ? » est présent sur l'écran de connexion ;
- la demande se fait uniquement avec le numéro de téléphone (aucune adresse email requise) ;
- la réponse de l'endpoint de demande est **neutre** : un numéro inconnu renvoie la même réponse
  HTTP 200 et le même message qu'un numéro connu (pas d'énumération de compte) ;
- l'OTP de réinitialisation est généré, **stocké haché**, à usage unique et expire après la durée
  configurée (`OTP_TTL_SECONDS`, défaut 300 s) ;
- un OTP expiré, erroné, déjà consommé ou appartenant à un autre usage (`purpose`) est refusé ;
- le nombre de tentatives de saisie de l'OTP est limité (`OTP_MAX_ATTEMPTS`) ; le dépassement
  invalide l'OTP et oblige à une nouvelle demande ;
- le renvoi est limité par numéro et par adresse IP (`OTP_RESEND_LIMIT_PER_PHONE`,
  `OTP_RESEND_LIMIT_PER_IP`) ;
- les endpoints de réinitialisation sont protégés par rate limiting ;
- le nouveau mot de passe respecte la politique de mot de passe validée **côté serveur** ; le
  nouveau mot de passe ne peut pas être identique à l'actuel ;
- après réinitialisation réussie : tous les refresh tokens de l'utilisateur sont révoqués, les
  sessions actives sont invalidées, un SMS de confirmation est envoyé et l'utilisateur est
  redirigé vers l'écran de connexion ;
- un utilisateur non confirmé (compte jamais activé) ne peut pas réinitialiser son mot de passe
  : il est renvoyé vers le flux d'activation ;
- la réinitialisation est refusée (ou sans effet) pour un compte désactivé ou supprimé
  logiquement ;
- aucun mot de passe, OTP ou hash n'apparaît en clair dans les logs, les réponses API ou les
  erreurs ;
- le parcours complet est utilisable sur mobile, y compris en connexion lente ;
- les tests couvrent : numéro inconnu, OTP invalide, OTP expiré, OTP réutilisé, dépassement de
  tentatives, renvoi contrôlé, mot de passe trop faible, réutilisation du mot de passe courant,
  révocation des sessions et journalisation.

**Définition de terminé :**

- code backend et frontend livré ;
- tests unitaires, API et E2E passants (parcours mobile inclus) ;
- endpoints protégés par rate limiting et journalisation vérifiés par les tests ;
- documentation du flux disponible (`docs/flows/authentication.md`) ;
- endpoints documentés dans le contrat API ;
- aucun secret fournisseur SMS dans le code ;
- revue de sécurité effectuée (énumération de compte, énumération par timing, révocation de
  session).

#### MVP-018 — Réinitialisation par email (variante facultative, P1/P2) 🆕

**Dépendances :** MVP-017 ; ajout facultatif de l'email ; envoi d'email configuré.

**Principe :** si l'utilisateur a ajouté et vérifié une adresse email depuis son dashboard, une
réinitialisation par lien email à usage unique est proposée **en complément** du canal SMS. Le
canal SMS reste la référence du MVP.

**Critères d'acceptation :**

- le lien de réinitialisation est à usage unique, signé, expiré (durée configurable) et invalidé
  après usage ou après changement de mot de passe ;
- la réponse de l'endpoint de demande est neutre (pas d'énumération) ;
- l'email n'est utilisé que s'il a été vérifié ;
- le comportement de révocation de session et de journalisation est identique à MVP-017 ;
- les tests couvrent lien expiré, lien réutilisé, email non vérifié et email absent.

### P1 — Nécessaire avant mise en production

#### MVP-013 — Compression et gestion des médias

**Dépendances :** MVP-007 ; stockage objet ou stockage média sécurisé ; Celery.

**Critères d'acceptation :**

- les images sont redimensionnées et compressées avant upload lorsque possible ;
- les thumbnails sont générés ;
- les listes ne chargent pas les originaux ;
- les limites de taille et de type sont appliquées ;
- les traitements échoués sont visibles et relançables.

**Définition de terminé :** tests sur appareils mobiles ; mesures de taille avant/après ;
traitement asynchrone opérationnel ; documentation du cycle média.

#### MVP-014 — Notifications et événements métier

**Dépendances :** MVP-006 ; MVP-008 ; MVP-010 ; Celery/Redis.

**Critères d'acceptation :**

- les événements métier sont émis selon des règles documentées ;
- les notifications in-app sont persistées ;
- les notifications similaires peuvent être regroupées ;
- les erreurs de traitement sont journalisées ;
- les permissions de consultation sont respectées.

**Définition de terminé :** tests d'événements et de notifications passants ; suivi des tâches
disponible ; aucun envoi multicanal obligatoire dans le MVP.

#### MVP-015 — Observabilité et healthchecks

**Dépendances :** Phase 1 ; Celery ; Redis ; PostgreSQL.

**Critères d'acceptation :**

- `/health/` vérifie les dépendances critiques ;
- les erreurs API sont structurées ;
- les temps de réponse sont mesurables ;
- les erreurs de synchronisation, uploads et tâches Celery sont suivies ;
- les logs ne contiennent pas de secrets ni d'OTP en clair.

**Définition de terminé :** documentation d'exploitation ; tests de healthcheck ; tableau de
suivi minimal ; procédure de diagnostic documentée.

#### MVP-016 — Tests E2E et données de seed

**Dépendances :** toutes les fonctionnalités P0.

**Critères d'acceptation :** les scénarios suivants passent automatiquement :

1. inscription avec numéro ;
2. validation OTP ;
3. connexion ;
4. ajout facultatif de l'email ;
5. création d'organisation ;
6. création de projet ;
7. ajout de membre ;
8. création de jalon ;
9. capture de preuve ;
10. validation de preuve ;
11. création de dépense ;
12. consultation du dashboard ;
13. passage offline ;
14. synchronisation automatique ;
15. **réinitialisation du mot de passe après oubli 🆕**.

**Définition de terminé :** tests exécutables en environnement documenté ; seed explicitement
identifié comme développement ; données réalistes en contexte camerounais ; aucune donnée mockée
dans le parcours produit final.

---

## 4. Dépendances critiques

**Chaîne d'accès**
Fondations → Utilisateur → OTP → Connexion → RBAC → Organisations et projets
**→ Réinitialisation du mot de passe (dépend de Utilisateur + OTP + sessions) 🆕**

**Chaîne de suivi opérationnel**
Projets → Jalons et tâches → Preuves terrain → Validation → Dashboard

**Chaîne offline**
Capture terrain → IndexedDB → File de synchronisation → Idempotence → Upload → Traitement média
→ Validation serveur

**Chaîne financière**
Projet → Budget → Dépense → Transaction atomique → Journalisation → Dashboard

**Chaîne d'exploitation**
Docker → PostgreSQL/Redis → Celery → Healthchecks → Logs → Métriques → Optimisation

Aucune fonctionnalité dépendant d'une chaîne non stabilisée ne doit être considérée comme
terminée.

---

## 5. Définition de terminé commune à toutes les fonctionnalités

Une fonctionnalité est terminée uniquement si :

- le besoin métier est documenté ;
- le modèle de données est validé ;
- l'API est implémentée et documentée ;
- l'UX couvre les états chargement, vide, erreur, permissions et hors ligne lorsque pertinent ;
- les permissions backend sont appliquées ;
- les validations d'entrée sont présentes ;
- les logs sensibles sont définis ;
- les tests unitaires et d'intégration sont passants ;
- le test E2E est ajouté lorsque le parcours est critique ;
- les requêtes SQL sont vérifiées pour éviter les N+1 ;
- la pagination est appliquée aux collections volumineuses ;
- les performances sont mesurées lorsque la fonctionnalité est utilisée fréquemment ;
- la documentation est mise à jour ;
- les données de seed sont cohérentes ;
- aucun bouton n'est fictif ;
- aucune donnée mockée ne subsiste dans le parcours final ;
- aucun secret n'est présent dans le code ;
- la revue de code est terminée ;
- la fonctionnalité est déployable avec Docker ;
- les critères d'acceptation mesurables sont tous satisfaits.

---

## 6. Hors périmètre du MVP

Les éléments suivants ne doivent pas bloquer la livraison du MVP :

- audits avancés ;
- rapports PDF ;
- notifications email, SMS ou WhatsApp avancées ;
- analytics avancées ;
- marketplace ;
- intégrations externes ;
- fonctionnalités avancées pour auditeurs indépendants ;
- chatbot générique ;
- analyse de risques par IA ;
- résumés génératifs ;
- détection d'anomalies par IA ;
- **questions secrètes / récupération de compte par support manuel (hors procédure documentée) 🆕** ;
- **envoi d'un mot de passe en clair par SMS ou email 🆕**.

Les anomalies du MVP doivent d'abord être détectées par des règles déterministes :

- dépense > budget ;
- retard > seuil configuré ;
- preuve hors périmètre ;
- doublon de fichier ou d'opération ;
- variation financière inhabituelle ;
- **plus de N échecs de connexion ou N demandes de réinitialisation sur une période donnée
  (détection d'abus, pas de scoring IA) 🆕**.

---

## 7. Validation finale du MVP

Le MVP est accepté lorsque :

- l'inscription fonctionne avec un numéro de téléphone uniquement ;
- l'email peut être ajouté ultérieurement depuis le dashboard ;
- les OTP sont expirables, limités, hachés et journalisés ;
- **un utilisateur qui a oublié son mot de passe peut le réinitialiser seul, depuis son
  téléphone, via un OTP SMS, et ses anciennes sessions sont révoquées 🆕** ;
- les permissions backend protègent toutes les données sensibles ;
- un projet peut être créé et partagé avec des membres autorisés ;
- les jalons, tâches et retards sont suivis ;
- une preuve peut être capturée sans connexion ;
- la preuve conserve GPS, timestamp, utilisateur, projet et hash lorsque disponibles ;
- la synchronisation reprend automatiquement après retour réseau ;
- les doublons sont empêchés par idempotence ;
- les budgets et dépenses sont calculés côté serveur ;
- les opérations financières critiques sont atomiques ;
- le dashboard affiche avancement, budget, dépenses, solde, jalons, preuves, alertes et activité ;
- les actions sensibles sont journalisées ;
- les collections sont paginées ;
- les parcours principaux ne présentent pas de N+1 ;
- les erreurs, synchronisations et tâches asynchrones sont observables ;
- les tests E2E critiques passent ;
- l'application reste utilisable sur mobile avec une connexion lente ou intermittente ;
- la documentation d'installation, d'architecture, d'API, de synchronisation et de déploiement
  est disponible.

---

## 8. Checklist de passage de phase

Avant chaque passage de phase, vérifier :

1. Les dépendances sont-elles satisfaites ?
2. Les critères d'acceptation sont-ils mesurés ?
3. Les tests sont-ils passants ?
4. Les permissions backend sont-elles vérifiées ?
5. Le comportement offline est-il documenté ?
6. Les performances sont-elles acceptables sur réseau 3G ?
7. Les données financières sont-elles cohérentes ?
8. Les logs et métriques permettent-ils de diagnostiquer les erreurs ?
9. La fonctionnalité apporte-t-elle une valeur métier réelle ?
10. La solution reste-t-elle simple et maintenable ?
11. **Un utilisateur bloqué (mot de passe oublié, téléphone perdu) dispose-t-il d'un parcours de
    récupération documenté ? 🆕**
