"""Synchronisation hors ligne (MVP-009).

Cette application porte le **socle serveur** de la reprise après coupure réseau :

* un registre d'idempotence (`SyncOperation`) : une clé déjà appliquée est rejouée au lieu
  d'être réexécutée — une même opération ne produit jamais deux effets ;
* `POST /api/sync/batch/` : rejeu d'un lot d'opérations (mises à jour de tâche/jalon, décisions
  de validation) lorsque l'appareil retrouve du réseau ;
* `GET /api/sync/status/` : ce que le serveur sait de la synchronisation (types supportés,
  opérations en cours ou bloquées) — utilisé par l'écran de suivi et par l'exploitation.

Les **fichiers** (photos de preuve) ne passent pas par le lot : ils sont envoyés un par un sur
`POST /api/evidences/` avec leur `Idempotency-Key`, ce qui autorise la reprise d'un envoi
interrompu sans doublon.
"""
