# Flux preuves terrain : capture, validation, historique (Phase 5 — MVP-007, 008)

Statut : implémenté et testé. Références : `docs/data-model.md` §4, `docs/api-contract.md` §6,
`docs/rbac-matrix.md`, `docs/offline-sync.md` (suite en phase 6).

## 1. Objets et vocabulaire

| Objet | Rôle | Champs structurants |
|---|---|---|
| **Preuve** (`Evidence`) | pièce photographique rattachée à un projet et à un auteur, horodatée | `file`, `hash_sha256`, `captured_at`, `received_at`, `latitude`/`longitude`/`gps_status`, `sync_status`, `status`, `idempotency_key` |
| **Décision** (`EvidenceValidation`) | trace d'un changement de statut, jamais modifiable | `actor`, `action`, `from_status`, `to_status`, `comment`, `created_at` |

Statuts de preuve : `PENDING`, `VALIDATED`, `REJECTED`, `FLAGGED`.
Statuts d'acheminement : `PENDING`, `UPLOADING`, `SYNCED`, `FAILED`, `CONFLICT` (renseignés côté
appareil ; le serveur écrit `SYNCED` à la réception).
Statut GPS : `AVAILABLE`, `UNAVAILABLE`, `DENIED` — un GPS indisponible n'empêche **jamais**
l'envoi, il est simplement enregistré comme tel.

## 2. Capture côté appareil (avant tout réseau)

1. L'utilisateur choisit/appareil-photo ; le fichier est **compressé sur l'appareil**
   (≤ 1600 px de côté, JPEG qualité 0,82) et l'empreinte **SHA-256** du résultat est calculée
   (WebCrypto). C'est cette empreinte qui partira au serveur : le hash est donc connu même si la
   photo est déposée plus tard, hors ligne.
2. La position est demandée **explicitement** (`getPosition`) ; un refus (`DENIED`) ou une
   indisponibilité (`UNAVAILABLE`) est affiché et n'empêche pas l'envoi.
3. Une **clé d'idempotence** est générée pour la tentative (`newIdempotencyKey`) et réutilisée
   telle quelle en cas de réessai.
4. Métadonnées jointes : modèle d'appareil, plateforme, version d'application, description,
   tâche éventuelle, horodatage de capture.

## 3. Envoi et règles serveur (jamais négociables côté client)

1. `POST /api/evidences/` en `multipart` avec l'en-tête `Idempotency-Key` **obligatoire**.
2. Le **contenu réel** du fichier est vérifié (magic bytes Pillow) : JPEG/PNG/WebP, ≤ 10 Mo,
   ≤ 4000 px. Le nom d'origine est ignoré ; le chemin est régénéré
   (`evidences/{project_id}/{yyyy}/{mm}/{uuid}.ext`).
3. `captured_at` dans le futur → `400 captured_at_in_future` (horloge de l'appareil).
4. **Rejeu** : même auteur + même clé → `200` + `Idempotency-Replayed: true` et la même preuve.
5. **Doublon** : même `hash_sha256` déjà présent dans le projet → `409 duplicate_evidence`,
   la preuve existante est renvoyée dans `details.evidence` (l'UI invite à la consulter au lieu
   de parler d'erreur technique).
6. **Périmètre** : si le projet a des coordonnées et que le GPS est disponible, la distance est
   calculée (Haversine) ; hors périmètre → `422 evidence_out_of_geofence` quand
   `EVIDENCE_GEOFENCE_ENFORCE` est actif (message : reprendre la photo sur site ou signaler).
7. À la réception : `status = PENDING`, `sync_status = SYNCED`, `ActivityLog EVIDENCE_CAPTURED`,
   puis génération asynchrone des dérivées (miniature WebP 320 px, version liste JPEG 1080 px).
   Un échec de dérivée **ne remet pas en cause** la preuve : l'API retombe sur l'original.

## 4. Décisions de validation

| Action | Depuis | Vers | Commentaire |
|---|---|---|---|
| `VALIDATE` | `PENDING`, `REJECTED`, `FLAGGED` | `VALIDATED` | facultatif |
| `REJECT` | `PENDING`, `VALIDATED`, `FLAGGED` | `REJECTED` | **obligatoire** |
| `FLAG` | `PENDING`, `VALIDATED` | `FLAGGED` | **obligatoire** |
| `REOPEN` | `VALIDATED`, `REJECTED`, `FLAGGED` | `PENDING` | facultatif |

Règles :
- seule une capacité `validate_evidence` permet de décider ; sinon `403` ;
- **personne ne valide sa propre preuve** (`403 cannot_validate_own_evidence`), sauf
  administrateur plateforme (supervision) ;
- toute action incompatible avec le statut courant renvoie `409 invalid_transition` avec la liste
  `allowed_actions` — l'UI n'affiche que les boutons réellement permis ;
- chaque décision écrit une ligne `EvidenceValidation` (immuable : `save()` sur une ligne
  existante ou `delete()` lèvent, y compris depuis l'administration) et un `ActivityLog`
  (`EVIDENCE_VALIDATED` / `REJECTED` / `FLAGGED` / `REOPENED`) ;
- une preuve rejetée reste **consultable** : elle n'est jamais supprimée, et son historique
  explique le refus.

## 5. Lecture : galerie, détail, file d'attente

- `GET /api/projects/{id}/evidences/` — galerie paginée avec `counts` par statut et filtres
  `status` (CSV), `sync_status`, `author`, `task`, `pending` ; chaque élément porte
  `distance_from_site_m`, `inside_geofence` et ses `permissions`.
- `GET /api/evidences/{id}/` — détail + dernière décision.
- `GET /api/evidences/{id}/history/` — historique paginé (acteur, date, action, commentaire).
- `GET /api/evidences/{id}/file/` et `/thumbnail/` — accès contrôlé par appartenance au projet
  (`404` pour un non-membre), `Cache-Control: private`, `X-Accel-Redirect` possible en production.
  Les URLs renvoyées sont **relatives** : elles restent valables derrière un proxy.
- `GET /api/evidences/pending/` — file d'attente du validateur tous projets confondus
  (`?older_than_hours=` pour mettre en avant ce qui traîne), sans les preuves qu'il ne peut pas
  décider (les siennes).

## 6. Expérience utilisateur (écran `/projets/{id}` → section Preuves)

- **Capture** : bouton photo, aperçu, gain de compression affiché (« 348 Ko → 96 Ko »), état GPS
  explicite, commentaire, bouton d'envoi désactivé tant qu'aucune photo n'est préparée.
- **Galerie** : vignettes (miniature servie par l'API), pastille de statut, compteurs par statut,
  états vides dédiés (aucune preuve, aucun historique).
- **Détail** : photo, métadonnées (auteur, appareil, horodatage, empreinte, distance), boutons de
  décision limités aux transitions autorisées par le backend, historique horodaté.
- **Messages** : chaque refus serveur (`duplicate_evidence`, `evidence_out_of_geofence`,
  `cannot_validate_own_evidence`, hors ligne) a un message métier dédié — jamais un code brut.

## 7. Suite hors ligne (phase 6 — MVP-009, implémenté)

La file locale (`frontend/src/lib/outbox.ts` + `src/lib/db.ts`) reprend exactement ce socle :

1. une capture sans réseau (ou dont l'envoi échoue) est **écrite dans IndexedDB** avec sa clé
   d'idempotence, le binaire de la photo (`ArrayBuffer`), les métadonnées et le statut local
   `PENDING` — elle apparaît immédiatement dans la galerie du chantier sous forme de carte
   locale ;
2. la reprise est **automatique** : au démarrage de l'application, sur l'événement `online`, au
   retour de l'onglet et après chaque mise en file (bouton « Synchroniser maintenant » en secours) ;
3. les photos repartent **une par une** sur `POST /api/evidences/` avec **la même** clé, donc
   jamais deux fois ; un doublon détecté par empreinte est traité comme un succès sans second
   envoi ;
4. les opérations sans fichier (décisions de validation, mises à jour de tâche ou de jalon)
   voyagent en lot sur `POST /api/sync/batch/` — voir `docs/api-contract.md` §6.1 ;
5. les refus sont classés `CONFLICT` (décision humaine) ou `FAILED` (nouvel essai avec délai
   croissant), et restent visibles sur l'écran `/synchronisation`.

Le détail de la stratégie (statuts, conflits, cache) est dans `docs/offline-sync.md`.
