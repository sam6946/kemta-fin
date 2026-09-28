#!/usr/bin/env python3
"""Test de charge ciblé de KEMTA SUIVI (phase 11) — bibliothèque standard uniquement.

Chaque « utilisateur virtuel » se connecte une fois (le login est limité à 10/min par IP : un
petit nombre de comptes suffit), puis enchaîne les requêtes d'un scénario réaliste pendant la
durée demandée. Le script mesure p50 / p95 / max par endpoint, le débit et le taux d'erreurs,
compare aux seuils, et retourne un **code de sortie non nul** si un seuil est dépassé — il peut
donc bloquer une régression en CI de préproduction.

Usage (données de charge : `manage.py seed_dev && manage.py seed_loadtest`) :

    python loadtest/run.py --base-url http://localhost:8000 --users 8 --duration 20

Le mot de passe se passe via l'environnement (jamais en argument ni dans le dépôt) :

    KEMTA_LOAD_PASSWORD='…' python loadtest/run.py …

Ne JAMAIS l'exécuter contre la production : il crée du trafic soutenu et consomme les quotas.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

# Comptes créés par `seed_dev` (développement uniquement) — voir docs/performance.md.
DEFAULT_PHONES = {
    "manager": "+237690000003",  # PROJECT_OWNER
    "engineer": "+237690000004",
    "finance": "+237690000008",
    "investor": "+237690000009",
}
DEFAULT_PASSWORD = "Kemta#2026Demo"  # mot de passe de démonstration documenté, dev uniquement

# Seuils par défaut (ms) sur p95 ; surchargeables via --p95-ms.
DEFAULT_P95_MS = 800
ERROR_RATE_MAX = 0.01


def scenario(project_id: int) -> list[tuple[str, str]]:
    """(nom, chemin) — un tour de la boucle d'un utilisateur. Aucun polling : lecture à la demande."""
    p = f"/api/projects/{project_id}"
    return [
        ("workspace", "/api/workspace/"),
        ("dashboard", f"{p}/dashboard/"),
        ("dashboard", f"{p}/dashboard/"),  # 2e appel : sert le cache
        ("evidences", f"{p}/evidences/"),
        ("milestones", f"{p}/milestones/"),
        ("tasks", f"{p}/tasks/"),
        ("expenses", f"{p}/expenses/"),
        ("notifications", "/api/notifications/"),
        ("unread-count", "/api/notifications/unread-count/"),
        ("evidences-pending", "/api/evidences/pending/"),
    ]


def request(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload, status = exc.read(), exc.code
    except Exception:
        return 0, b"", (time.perf_counter() - started) * 1000
    return status, payload, (time.perf_counter() - started) * 1000


def login(base: str, phone: str, password: str) -> str:
    status, payload, _ = request(
        base, "POST", "/api/auth/login/", body={"phone": phone, "password": password}
    )
    if status != 200:
        raise SystemExit(f"Connexion impossible pour {phone} (HTTP {status}) : {payload[:200]!r}")
    return json.loads(payload)["access"]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[index]


def worker(base, token, project_id, deadline, results, lock, errors):
    steps = scenario(project_id)
    while time.monotonic() < deadline:
        for name, path in steps:
            if time.monotonic() >= deadline:
                break
            status, _, elapsed = request(base, "GET", path, token)
            with lock:
                results[name].append(elapsed)
                if status == 0 or (status >= 400 and status not in (403, 404)):
                    errors[f"{name}:{status}"] += 1
                # 403/404 : un rôle sans accès à une ressource est une réponse valide, pas une erreur.


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--users", type=int, default=8, help="utilisateurs virtuels simultanés")
    parser.add_argument("--duration", type=int, default=20, help="durée en secondes")
    parser.add_argument("--project", type=int, default=1, help="identifiant du projet chargé")
    parser.add_argument(
        "--p95-ms", type=float, default=DEFAULT_P95_MS, help="seuil p95 par endpoint"
    )
    parser.add_argument("--json", dest="json_out", help="écrit le rapport JSON dans ce fichier")
    args = parser.parse_args()

    password = os.environ.get("KEMTA_LOAD_PASSWORD", DEFAULT_PASSWORD)
    base = args.base_url.rstrip("/")

    status, _, _ = request(base, "GET", "/api/health/")
    if status != 200:
        print(f"Serveur indisponible ({base}/api/health/ → {status}).", file=sys.stderr)
        return 2

    tokens = {role: login(base, phone, password) for role, phone in DEFAULT_PHONES.items()}
    roles = list(tokens)
    results: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    lock = threading.Lock()
    started = time.monotonic()
    deadline = started + args.duration
    threads = [
        threading.Thread(
            target=worker,
            args=(
                base,
                tokens[roles[i % len(roles)]],
                args.project,
                deadline,
                results,
                lock,
                errors,
            ),
            daemon=True,
        )
        for i in range(args.users)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started

    total = sum(len(v) for v in results.values())
    error_total = sum(errors.values())
    print(
        f"\n{args.users} utilisateurs, {elapsed:.1f} s, {total} requêtes, {total / elapsed:.1f} req/s"
    )
    print(f"{'endpoint':<20}{'n':>7}{'moy':>9}{'p50':>9}{'p95':>9}{'max':>9}  (ms)")
    failed = []
    report = {}
    for name in sorted(results):
        values = results[name]
        p95 = percentile(values, 95)
        report[name] = {
            "n": len(values),
            "mean_ms": round(statistics.fmean(values), 1),
            "p50_ms": round(percentile(values, 50), 1),
            "p95_ms": round(p95, 1),
            "max_ms": round(max(values), 1),
        }
        flag = "  ✗ SEUIL" if p95 > args.p95_ms else ""
        if flag:
            failed.append(f"{name}: p95 {p95:.0f} ms > {args.p95_ms:.0f} ms")
        r = report[name]
        print(
            f"{name:<20}{r['n']:>7}{r['mean_ms']:>9}{r['p50_ms']:>9}{r['p95_ms']:>9}{r['max_ms']:>9}{flag}"
        )
    rate = error_total / total if total else 1.0
    print(f"\nErreurs (5xx, 401, 429, réseau, 4xx inattendus) : {error_total} ({rate:.2%})")
    for key, count in sorted(errors.items()):
        print(f"  {key}: {count}")
    if rate > ERROR_RATE_MAX:
        failed.append(f"taux d'erreurs {rate:.2%} > {ERROR_RATE_MAX:.0%}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "users": args.users,
                    "duration_s": round(elapsed, 1),
                    "requests": total,
                    "errors": error_total,
                    "endpoints": report,
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )
    if failed:
        print("\nÉCHEC :", *failed, sep="\n  - ")
        return 1
    print("\nOK : tous les seuils sont respectés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
