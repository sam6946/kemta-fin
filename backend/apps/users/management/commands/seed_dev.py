"""Jeu de données de **développement** : un compte de démonstration par rôle.

- Refuse de s'exécuter si `DJANGO_ENV=production` (sauf `--force`).
- Aucune donnée n'est simulée côté produit : ces comptes servent aux démos et aux tests.
- Organisations, projets, membres, jalons et tâches de démonstration.
- Preuves terrain de démonstration (phase 5) ; les lignes budgétaires viendront en phase 7.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify as _slugify

from apps.users.models import User
from apps.users.roles import ALL_ROLES, ROLE_LABELS
from apps.users.services.phone import normalize_phone

# Mot de passe de démonstration : documenté, jamais utilisé hors développement.
DEMO_PASSWORD = "Kemta#2026Demo"

DEMO_PEOPLE = [
    ("PLATFORM_ADMIN", "+237690000001", "Awa", "Mbarga"),
    ("ORG_OWNER", "+237690000002", "Serge", "Kamdem"),
    ("PROJECT_OWNER", "+237690000003", "Arnaud", "Nkoulou"),
    ("ENGINEER", "+237690000004", "Bertrand", "Fotso"),
    ("CONTRACTOR", "+237690000005", "Chantal", "Ekwalla"),
    ("FIELD_AGENT", "+237690000006", "Didier", "Ngassa"),
    ("VALIDATOR", "+237690000007", "Estelle", "Mvondo"),
    ("FINANCE", "+237690000008", "Franck", "Biya"),
    ("INVESTOR", "+237690000009", "Georgette", "Tchoumi"),
]


ORGANIZATIONS = [
    {
        "key": "promoteur",
        "name": "KEMTA Promotion Douala",
        "type": "PROMOTER",
        "city": "Douala",
        "owner_role": "ORG_OWNER",
    },
    {
        "key": "pme",
        "name": "BTP Kribi SARL",
        "type": "PME",
        "city": "Kribi",
        "owner_role": "CONTRACTOR",
    },
    {
        "key": "be",
        "name": "Ingénierie Littoral",
        "type": "ENGINEERING_FIRM",
        "city": "Douala",
        "owner_role": "ENGINEER",
    },
]

# Projets réalistes (contexte camerounais) — montants entiers en FCFA.
PROJECTS = [
    {
        "organization": "promoteur",
        "name": "Résidence Bonamoussadi — tranche 1",
        "code": "RBS-T1",
        "city": "Douala",
        "region": "Littoral",
        "location_label": "Bonamoussadi, avenue principale",
        "latitude": "4.089100",
        "longitude": "9.740600",
        "budget_total": 85_000_000,
        "status": "ACTIVE",
        "planned_start_date": "2026-01-12",
        "planned_end_date": "2026-12-18",
        "members": {
            "PROJECT_OWNER": {},
            "ENGINEER": {},
            "CONTRACTOR": {},
            "FIELD_AGENT": {},
            "VALIDATOR": {"can_validate_evidence": True},
            "FINANCE": {"can_manage_finance": True},
            "INVESTOR": {},
        },
    },
    {
        "organization": "promoteur",
        "name": "Immeuble Akwa — tranche 2",
        "code": "AKW-T2",
        "city": "Douala",
        "region": "Littoral",
        "location_label": "Akwa, rue Castelnau",
        "latitude": "4.051100",
        "longitude": "9.767900",
        "budget_total": 145_000_000,
        "status": "ACTIVE",
        "planned_start_date": "2026-03-02",
        "planned_end_date": "2027-06-30",
        "members": {"PROJECT_OWNER": {}, "ENGINEER": {}, "FINANCE": {"can_manage_finance": True}},
    },
    {
        "organization": "pme",
        "name": "Voie de contournement Kribi",
        "code": "VCK-01",
        "city": "Kribi",
        "region": "Sud",
        "location_label": "Entrée nord de Kribi",
        "latitude": "2.937300",
        "longitude": "9.910000",
        "budget_total": 320_000_000,
        "status": "ACTIVE",
        "planned_start_date": "2025-11-03",
        "planned_end_date": "2026-11-30",
        "members": {
            "PROJECT_OWNER": {},
            "CONTRACTOR": {},
            "FIELD_AGENT": {},
            "FINANCE": {"can_manage_finance": True},
        },
    },
    {
        "organization": "be",
        "name": "Réhabilitation école de Nkolbisson",
        "code": "REC-NKB",
        "city": "Yaoundé",
        "region": "Centre",
        "location_label": "Nkolbisson, quartier Marché",
        "latitude": "3.872000",
        "longitude": "11.450000",
        "budget_total": 22_500_000,
        "status": "DRAFT",
        "planned_start_date": "2026-02-16",
        "planned_end_date": "2026-08-28",
        "members": {"ENGINEER": {}, "VALIDATOR": {"can_validate_evidence": True}},
    },
]


# Preuves terrain de démonstration : couleurs distinctes pour que la galerie soit lisible.
EVIDENCES = {
    "RBS-T1": [
        {
            "role": "FIELD_AGENT",
            "description": "Ferraillage des semelles — avant coulage",
            "days_ago": 34,
            "color": (196, 178, 148),
            "status": "VALIDATED",
        },
        {
            "role": "FIELD_AGENT",
            "description": "Coffrage de la dalle niveau 2",
            "days_ago": 6,
            "color": (150, 160, 170),
        },
        {
            "role": "FIELD_AGENT",
            "description": "Vue d'ensemble du chantier depuis la voie d'accès",
            "days_ago": 3,
            "color": (110, 140, 110),
        },
    ],
    "AKW-T2": [
        {
            "role": "ENGINEER",
            "description": "Contrôle des plans d'exécution sur site",
            "days_ago": 20,
            "color": (170, 170, 200),
            "status": "VALIDATED",
        },
        {
            "role": "CONTRACTOR",
            "description": "Armatures du poteau P12",
            "days_ago": 8,
            "color": (188, 160, 130),
        },
    ],
    "VCK-01": [
        {
            "role": "FIELD_AGENT",
            "description": "Remblai compacté PK3 — contrôle de niveaux",
            "days_ago": 5,
            "color": (170, 150, 120),
            "status": "REJECTED",
        },
        {
            "role": "CONTRACTOR",
            "description": "Aire de stockage des graves",
            "days_ago": 2,
            "color": (140, 140, 135),
        },
    ],
    "REC-NKB": [
        {
            "role": "ENGINEER",
            "description": "Toiture du bâtiment principal — état actuel",
            "days_ago": 12,
            "color": (160, 140, 120),
            "status": "FLAGGED",
        },
    ],
}


# Budget et dépenses de démonstration (phase 7) : les montants sont écrits par les **services**
# financiers (jamais à la main), donc le grand livre, les seuils et le journal restent cohérents.
#
# Statuts couverts : brouillon, soumise, approuvée, payée, rejetée et annulée (contre-écriture).
FINANCE_PLAN = {
    "RBS-T1": {
        "lines": [
            ("Matériaux de construction", "MATERIALS", 32_000_000, "Ciment, fer à béton, agrégats"),
            ("Main-d'œuvre", "LABOUR", 18_000_000, "Maçonnerie, ferraillage, finitions"),
            ("Matériel et engins", "EQUIPMENT", 12_000_000, "Location grue, bétonnières"),
            ("Transport et carburant", "TRANSPORT", 6_000_000, ""),
            ("Frais administratifs", "ADMIN", 4_000_000, ""),
            ("Sous-traitance électricité", "SUBCONTRACT", 8_000_000, ""),
        ],
        "expenses": [
            {
                "title": "Ciment CIMENCAM — 800 sacs de 50 kg",
                "amount": 4_800_000,
                "line": "Matériaux de construction",
                "incurred_offset": -38,
                "supplier": "CIMENCAM Douala",
                "invoice_number": "FAC-2026-0141",
                "status": "PAID",
                "receipt": "facture-cimencam-0141.pdf",
                "payments": [
                    {
                        "amount": 500_000,
                        "method": "CASH",
                        "reference": "AV-0141",
                        "offset": -37,
                        "cancel": "Avance encaissée en double : remboursée au fournisseur.",
                    },
                    {
                        "amount": 4_800_000,
                        "method": "BANK_TRANSFER",
                        "reference": "VIR-2026-0141",
                        "offset": -35,
                    },
                ],
            },
            {
                "title": "Fer à béton HA 12 — 12 tonnes",
                "amount": 5_200_000,
                "line": "Matériaux de construction",
                "incurred_offset": -26,
                "supplier": "Métal Plus Douala",
                "invoice_number": "FAC-2026-0207",
                "status": "APPROVED",
                "payments": [
                    {
                        "amount": 2_000_000,
                        "method": "MOBILE_MONEY",
                        "reference": "MM-77231",
                        "offset": -20,
                    }
                ],
            },
            {
                "title": "Main-d'œuvre — semaine 6 (6 maçons)",
                "amount": 3_000_000,
                "line": "Main-d'œuvre",
                "incurred_offset": -9,
                "supplier": "Équipe maçonnerie Nkoulou",
                "invoice_number": "",
                "status": "SUBMITTED",
            },
            {
                "title": "Location grue mobile 25 t — 3 jours",
                "amount": 1_800_000,
                "line": "Matériel et engins",
                "incurred_offset": -2,
                "supplier": "Engins Littoral",
                "invoice_number": "DEV-2026-118",
                "status": "DRAFT",
            },
            {
                "title": "Carburant engins — semaine 5",
                "amount": 650_000,
                "line": "Transport et carburant",
                "incurred_offset": -14,
                "supplier": "Station Tradex Bonabéri",
                "invoice_number": "TX-2026-5512",
                "status": "REJECTED",
                "reject_comment": "Facture sans bon de livraison : à reprendre avec le bordereau.",
            },
            {
                "title": "Étude géotechnique complémentaire",
                "amount": 1_200_000,
                "line": "Frais administratifs",
                "incurred_offset": -30,
                "supplier": "GéoSol Cameroun",
                "invoice_number": "FAC-2026-0088",
                "status": "CANCELLED",
                "cancel_comment": "Prestation finalement assurée par le bureau d'études interne.",
            },
        ],
    },
    "AKW-T2": {
        "lines": [
            ("Gros œuvre — fondations et structure", "MATERIALS", 62_000_000, ""),
            ("Second œuvre", "LABOUR", 30_000_000, ""),
            ("Études et contrôle", "SUBCONTRACT", 25_000_000, ""),
            ("Matériel et engins", "EQUIPMENT", 20_000_000, ""),
            ("Frais administratifs", "ADMIN", 5_000_000, ""),
        ],
        # 118 M engagés sur 145 M : la démo montre l'alerte de seuil (81 %) **et** un poste dépassé.
        "expenses": [
            {
                "title": "SOGEA — fondations et structure niveau 1",
                "amount": 95_000_000,
                "line": "Gros œuvre — fondations et structure",
                "incurred_offset": -22,
                "supplier": "SOGEA Cameroun",
                "invoice_number": "FAC-SOG-2026-118",
                "status": "APPROVED",
                "override_reason": "Avenant n°1 validé par la maîtrise d'ouvrage (gros œuvre révisé).",
                "receipt": "situation-sogea-118.pdf",
                "payments": [
                    {
                        "amount": 60_000_000,
                        "method": "BANK_TRANSFER",
                        "reference": "VIR-SOG-118",
                        "offset": -15,
                    }
                ],
            },
            {
                "title": "Études d'exécution béton armé",
                "amount": 23_000_000,
                "line": "Études et contrôle",
                "incurred_offset": -11,
                "supplier": "Ingénierie Littoral",
                "invoice_number": "FAC-BET-2026-044",
                "status": "APPROVED",
            },
        ],
    },
    "VCK-01": {
        "lines": [
            ("Terrassement et couche de forme", "MATERIALS", 45_000_000, ""),
            ("Couche de base — latérite traitée", "MATERIALS", 80_000_000, ""),
            ("Revêtement bitumineux", "SUBCONTRACT", 120_000_000, ""),
            ("Signalisation et sécurité", "OTHER", 25_000_000, ""),
            ("Frais généraux de chantier", "ADMIN", 15_000_000, ""),
        ],
        "expenses": [
            {
                "title": "Terrassement phase 1 — 4 km",
                "amount": 40_000_000,
                "line": "Terrassement et couche de forme",
                "incurred_offset": -18,
                "supplier": "BTP Kribi SARL",
                "invoice_number": "SIT-2026-007",
                "status": "APPROVED",
                "payments": [
                    {
                        "amount": 20_000_000,
                        "method": "BANK_TRANSFER",
                        "reference": "VIR-KRI-007",
                        "offset": -12,
                    }
                ],
            },
            {
                "title": "Fourniture latérite — 3 000 m³",
                "amount": 12_000_000,
                "line": "Couche de base — latérite traitée",
                "incurred_offset": -5,
                "supplier": "Carrière de Lolabé",
                "invoice_number": "FAC-LOL-2026-031",
                "status": "SUBMITTED",
            },
        ],
    },
    "REC-NKB": {
        "lines": [
            ("Réfection toiture et menuiserie", "MATERIALS", 9_000_000, ""),
            ("Peinture et finitions", "LABOUR", 4_500_000, ""),
        ],
        "expenses": [
            {
                "title": "Tôle bac alu — devis fournisseur",
                "amount": 4_200_000,
                "line": "Réfection toiture et menuiserie",
                "incurred_offset": -3,
                "supplier": "Quincaillerie Nkolbisson",
                "invoice_number": "DEV-NKB-2026-014",
                "status": "DRAFT",
            }
        ],
    },
}


# Planning de démonstration : offsets en jours par rapport à aujourd'hui (dates cohérentes et
# retards reproductibles), poids de pondération, responsables désignés par rôle.
PLANNING = {
    "RBS-T1": [
        {
            "title": "Installation de chantier",
            "status": "DONE",
            "planned_offset": -120,
            "actual_offset": -118,
            "weight": 1,
            "tasks": [
                {
                    "title": "Clôture et base vie",
                    "status": "DONE",
                    "weight": 1,
                    "start_offset": -120,
                    "end_offset": -114,
                    "actual_offset": -113,
                    "role": "FIELD_AGENT",
                },
                {
                    "title": "Raccordement eau et électricité",
                    "status": "DONE",
                    "weight": 1,
                    "start_offset": -115,
                    "end_offset": -108,
                    "actual_offset": -107,
                    "role": "CONTRACTOR",
                },
            ],
        },
        {
            "title": "Fondations et soubassement",
            "status": "DONE",
            "planned_offset": -60,
            "actual_offset": -52,
            "weight": 2,
            "tasks": [
                {
                    "title": "Terrassement",
                    "status": "DONE",
                    "weight": 2,
                    "start_offset": -100,
                    "end_offset": -75,
                    "actual_offset": -74,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Semelles et longrines",
                    "status": "DONE",
                    "weight": 3,
                    "start_offset": -74,
                    "end_offset": -58,
                    "actual_offset": -52,
                    "role": "CONTRACTOR",
                },
            ],
        },
        {
            "title": "Élévation des niveaux",
            "status": "IN_PROGRESS",
            "planned_offset": 45,
            "weight": 3,
            "tasks": [
                {
                    "title": "Poteaux niveau 1",
                    "status": "DONE",
                    "weight": 2,
                    "start_offset": -50,
                    "end_offset": -30,
                    "actual_offset": -29,
                    "role": "CONTRACTOR",
                },
                # Tâche en retard : fin prévue dépassée, statut non terminal.
                {
                    "title": "Dalle niveau 2",
                    "status": "IN_PROGRESS",
                    "progress": 60,
                    "weight": 3,
                    "start_offset": -28,
                    "end_offset": -5,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Maçonnerie niveau 2",
                    "status": "IN_PROGRESS",
                    "progress": 20,
                    "weight": 2,
                    "start_offset": -10,
                    "end_offset": 20,
                    "role": "CONTRACTOR",
                },
            ],
        },
        {
            "title": "Second œuvre et réception",
            "status": "PLANNED",
            "planned_offset": 150,
            "weight": 2,
            "tasks": [
                {
                    "title": "Électricité et plomberie",
                    "status": "TODO",
                    "weight": 2,
                    "start_offset": 30,
                    "end_offset": 90,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Visite de réception",
                    "status": "TODO",
                    "weight": 1,
                    "start_offset": 130,
                    "end_offset": 150,
                    "role": "VALIDATOR",
                },
            ],
        },
    ],
    "AKW-T2": [
        {
            "title": "Études d'exécution",
            "status": "DONE",
            "planned_offset": -90,
            "actual_offset": -85,
            "weight": 1,
            "tasks": [
                {
                    "title": "Plans béton armé",
                    "status": "DONE",
                    "weight": 2,
                    "start_offset": -90,
                    "end_offset": -70,
                    "actual_offset": -68,
                    "role": "ENGINEER",
                },
            ],
        },
        {
            "title": "Gros œuvre",
            "status": "IN_PROGRESS",
            "planned_offset": 60,
            "weight": 4,
            "tasks": [
                {
                    "title": "Fondations spéciales",
                    "status": "DONE",
                    "weight": 3,
                    "start_offset": -65,
                    "end_offset": -35,
                    "actual_offset": -33,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Structure niveau 1",
                    "status": "IN_PROGRESS",
                    "progress": 45,
                    "weight": 3,
                    "start_offset": -30,
                    "end_offset": 25,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Essais béton 28 jours",
                    "status": "TODO",
                    "weight": 1,
                    "start_offset": 5,
                    "end_offset": 40,
                    "role": "ENGINEER",
                    "depends_on": ["Structure niveau 1"],
                },
            ],
        },
        {
            "title": "Clos et couvert",
            "status": "PLANNED",
            "planned_offset": 210,
            "weight": 2,
            "tasks": [
                {
                    "title": "Charpente et couverture",
                    "status": "TODO",
                    "weight": 2,
                    "start_offset": 80,
                    "end_offset": 140,
                    "role": "CONTRACTOR",
                },
            ],
        },
    ],
    "VCK-01": [
        {
            "title": "Ouverture de la plateforme",
            "status": "DONE",
            "planned_offset": -150,
            "actual_offset": -140,
            "weight": 2,
            "tasks": [
                {
                    "title": "Débroussaillage",
                    "status": "DONE",
                    "weight": 2,
                    "start_offset": -150,
                    "end_offset": -120,
                    "actual_offset": -118,
                    "role": "CONTRACTOR",
                },
            ],
        },
        {
            # Jalon en retard : date prévue dépassée, statut non terminal.
            "title": "Terrassement général",
            "status": "IN_PROGRESS",
            "planned_offset": -12,
            "weight": 3,
            "tasks": [
                {
                    "title": "Décapage PK0 à PK2",
                    "status": "DONE",
                    "weight": 2,
                    "start_offset": -110,
                    "end_offset": -80,
                    "actual_offset": -78,
                    "role": "CONTRACTOR",
                },
                {
                    "title": "Remblais PK2 à PK5",
                    "status": "IN_PROGRESS",
                    "progress": 35,
                    "weight": 3,
                    "start_offset": -70,
                    "end_offset": 30,
                    "role": "CONTRACTOR",
                },
            ],
        },
        {
            "title": "Couche de fondation",
            "status": "PLANNED",
            "planned_offset": 120,
            "weight": 2,
            "tasks": [
                {
                    "title": "Grave non traitée",
                    "status": "TODO",
                    "weight": 2,
                    "start_offset": 35,
                    "end_offset": 100,
                    "role": "CONTRACTOR",
                },
            ],
        },
    ],
    "REC-NKB": [
        {
            "title": "Réception du chantier",
            "status": "PLANNED",
            "planned_offset": 25,
            "weight": 1,
            "tasks": [
                {
                    "title": "Relevé de l'état des lieux",
                    "status": "TODO",
                    "weight": 1,
                    "start_offset": 5,
                    "end_offset": 15,
                    "role": "ENGINEER",
                },
            ],
        },
    ],
}


class Command(BaseCommand):
    help = "Crée les comptes de démonstration (données de développement uniquement)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Autorise l'exécution même si DJANGO_ENV=production (déconseillé).",
        )
        parser.add_argument(
            "--password",
            default=DEMO_PASSWORD,
            help="Mot de passe des comptes de démonstration (développement uniquement).",
        )
        parser.add_argument(
            "--skip-projects",
            action="store_true",
            help="Ne créer que les comptes (sans organisations, projets ni membres).",
        )
        parser.add_argument(
            "--skip-finance",
            action="store_true",
            help="Ne pas créer le budget, les dépenses et les paiements de démonstration.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if getattr(settings, "ENV", "local") == "production" and not options["force"]:
            raise CommandError(
                "Refusé : DJANGO_ENV=production. Utilisez --force en connaissance de cause."
            )

        created = 0
        for role, raw_phone, first_name, last_name in DEMO_PEOPLE:
            phone, error = normalize_phone(raw_phone)
            if error:
                raise CommandError(f"Numéro de démonstration invalide : {raw_phone} ({error})")

            user, is_new = User.objects.get_or_create(
                phone=phone,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "is_active": True,
                    "is_phone_verified": True,
                },
            )
            if is_new:
                user.set_password(options["password"])
                user.save(update_fields=["password"])
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} compte(s) créé(s) sur {len(DEMO_PEOPLE)}. "
                f"Mot de passe : {options['password']} (développement uniquement)."
            )
        )
        if not options["skip_projects"]:
            (
                organizations,
                projects,
                memberships,
                milestones,
                tasks,
                evidences,
                finance,
            ) = self._seed_projects(with_finance=not options["skip_finance"])
            self.stdout.write(
                f"{organizations} organisation(s), {projects} projet(s), "
                f"{memberships} appartenance(s), {milestones} jalon(s), {tasks} tâche(s), "
                f"{evidences} preuve(s) créés."
            )
            if not options["skip_finance"]:
                self.stdout.write(
                    f"{finance['lines']} poste(s) budgétaire(s), {finance['expenses']} dépense(s), "
                    f"{finance['payments']} paiement(s), {finance['receipts']} justificatif(s) créés."
                )

        if ALL_ROLES:
            self.stdout.write("Rôles disponibles : " + ", ".join(ROLE_LABELS[r] for r in ALL_ROLES))
        self.stdout.write(
            self.style.WARNING(
                "Données de DÉVELOPPEMENT : ne pas exécuter sur une base de production."
            )
        )

    # ------------------------------------------------------------------
    # Organisations, projets et membres de démonstration (phase 3)
    # ------------------------------------------------------------------
    @staticmethod
    def _day(offset):
        """Date relative à aujourd'hui : les retards de démonstration restent stables."""
        if offset is None:
            return None
        return timezone.localdate() + timedelta(days=offset)

    # ------------------------------------------------------------------
    # Budget, dépenses et paiements de démonstration (phase 7)
    # ------------------------------------------------------------------
    @staticmethod
    def _finance_actors(project, users_by_role):
        """Deux acteurs distincts pour la démonstration : créer puis approuver.

        La séparation des tâches est une règle produit : on choisit donc un créateur et un
        approbateur différents quand le projet le permet, avec l'administrateur plateforme
        en dernier recours (projets de démonstration sans responsable financier).
        """
        from apps.projects.models import ProjectMember
        from apps.users.roles import Role

        preference = (Role.FINANCE, Role.PROJECT_OWNER, Role.ORG_OWNER)
        members = list(
            ProjectMember.objects.filter(project=project, is_active=True).select_related("user")
        )
        by_role = {member.role: member.user for member in members}

        def pick(exclude=None):
            for role in preference:
                candidate = by_role.get(role)
                if candidate is not None and candidate != exclude:
                    return candidate
            return next(
                (
                    member.user
                    for member in members
                    if member.can_manage_finance and member.user != exclude
                ),
                None,
            )

        fallback = users_by_role.get(Role.PLATFORM_ADMIN)
        creator = pick() or fallback
        approver = pick(exclude=creator) or fallback
        return creator, approver

    @staticmethod
    def _receipt_file(filename: str):
        """Justificatif de démonstration : un PDF minimal, généré en mémoire (aucun binaire suivi)."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        payload = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n"
        )
        return SimpleUploadedFile(filename, payload, content_type="application/pdf")

    @transaction.atomic
    def _seed_finance(self, projects_by_code: dict) -> dict:
        """Budget, dépenses et paiements de démonstration — **idempotent**.

        Tout passe par `apps.finance.services` : engagements, seuils et journal d'activité sont
        la conséquence des règles métier (jamais d'un `INSERT` direct), donc les totaux affichés
        par la synthèse correspondent exactement au grand livre.
        """
        from apps.core.exceptions import KemtaAPIError
        from apps.finance.models import BudgetLine, Expense, ExpenseStatus
        from apps.finance.services import (
            attach_receipt,
            cancel_payment,
            create_budget_line,
            create_expense,
            register_payment,
            transition_expense,
        )

        users_by_role = {user.role: user for user in User.objects.all()}
        counts = {"lines": 0, "expenses": 0, "payments": 0, "receipts": 0}

        for code, plan in FINANCE_PLAN.items():
            project = projects_by_code.get(code)
            if project is None:
                continue
            creator, approver = self._finance_actors(project, users_by_role)
            if creator is None:
                continue

            lines_by_label: dict[str, BudgetLine] = {}
            for index, (label, category, planned, notes) in enumerate(plan["lines"]):
                line = BudgetLine.objects.filter(project=project, label=label).first()
                if line is None:
                    line = create_budget_line(
                        project=project,
                        actor=creator,
                        data={
                            "label": label,
                            "category": category,
                            "planned_amount": planned,
                            "order": index,
                            "notes": notes,
                        },
                    )
                    counts["lines"] += 1
                lines_by_label[label] = line

            for spec in plan["expenses"]:
                guard = {"project": project}
                if spec.get("invoice_number"):
                    guard["invoice_number"] = spec["invoice_number"]
                else:
                    guard["title"] = spec["title"]
                if Expense.objects.filter(**guard).exists():
                    continue

                expense = create_expense(
                    project=project,
                    actor=creator,
                    data={
                        "title": spec["title"],
                        "description": spec.get("description", ""),
                        "amount": spec["amount"],
                        "incurred_on": self._day(spec["incurred_offset"]),
                        "budget_line": lines_by_label.get(spec.get("line")),
                        "supplier": spec.get("supplier", ""),
                        "invoice_number": spec.get("invoice_number", ""),
                    },
                )
                counts["expenses"] += 1

                if spec.get("receipt"):
                    attach_receipt(
                        expense=expense,
                        actor=creator,
                        upload=self._receipt_file(spec["receipt"]),
                    )
                    counts["receipts"] += 1

                status = spec["status"]
                if status != ExpenseStatus.DRAFT:
                    transition_expense(expense=expense, actor=creator, action="SUBMIT")
                if status in {
                    ExpenseStatus.APPROVED,
                    ExpenseStatus.PAID,
                    ExpenseStatus.CANCELLED,
                }:
                    try:
                        transition_expense(
                            expense=expense,
                            actor=approver,
                            action="APPROVE",
                            override_reason=spec.get("override_reason", ""),
                        )
                    except KemtaAPIError as error:  # pragma: no cover - garde-fou de seed
                        raise CommandError(
                            f"Approbation refusée ({code} / {spec['title']}) : {error.message}"
                        ) from error
                elif status == ExpenseStatus.REJECTED:
                    transition_expense(
                        expense=expense,
                        actor=approver,
                        action="REJECT",
                        comment=spec.get("reject_comment", ""),
                    )

                for payment_spec in spec.get("payments", []):
                    try:
                        payment = register_payment(
                            expense=expense,
                            actor=approver,
                            data={
                                "amount": payment_spec["amount"],
                                "paid_on": self._day(payment_spec["offset"]),
                                "method": payment_spec["method"],
                                "reference": payment_spec.get("reference", ""),
                            },
                        )
                    except KemtaAPIError as error:  # pragma: no cover - garde-fou de seed
                        raise CommandError(
                            f"Paiement de démonstration refusé ({code} / {spec['title']}) : "
                            f"{error.message}"
                        ) from error
                    counts["payments"] += 1
                    if payment_spec.get("cancel"):
                        cancel_payment(
                            payment=payment,
                            actor=approver,
                            reason=payment_spec["cancel"],
                        )

                if status == ExpenseStatus.CANCELLED:
                    transition_expense(
                        expense=expense,
                        actor=approver,
                        action="CANCEL",
                        comment=spec.get("cancel_comment", ""),
                    )

        return counts

    @transaction.atomic
    def _seed_planning(self, projects_by_code: dict) -> tuple[int, int]:
        """Jalons et tâches de démonstration, idempotents (clé : projet + titre)."""
        from decimal import Decimal

        from apps.projects.models import Milestone, ProjectMember, Task
        from apps.projects.progress import recalculate_project_progress

        users_by_role = {user.role: user for user in User.objects.all()}
        created_milestones = 0
        created_tasks = 0

        for code, milestones in PLANNING.items():
            project = projects_by_code.get(code)
            if project is None:
                continue
            tasks_by_title: dict[str, object] = {}
            for index, spec in enumerate(milestones):
                milestone, created = Milestone.objects.get_or_create(
                    project=project,
                    title=spec["title"],
                    defaults={
                        "status": spec["status"],
                        "planned_date": self._day(spec.get("planned_offset")),
                        "actual_date": self._day(spec.get("actual_offset")),
                        "order": index,
                        "weight": Decimal(str(spec.get("weight", 1))),
                        "created_by": project.created_by,
                    },
                )
                created_milestones += int(created)

                for task_spec in spec["tasks"]:
                    assignee = users_by_role.get(task_spec.get("role", ""))
                    # Seuls les membres actifs du projet peuvent être responsables.
                    if (
                        assignee is not None
                        and not ProjectMember.objects.filter(
                            project=project, user=assignee, is_active=True
                        ).exists()
                    ):
                        assignee = None
                    task, task_created = Task.objects.get_or_create(
                        project=project,
                        title=task_spec["title"],
                        defaults={
                            "milestone": milestone,
                            "status": task_spec["status"],
                            "planned_start_date": self._day(task_spec.get("start_offset")),
                            "planned_end_date": self._day(task_spec.get("end_offset")),
                            "actual_end_date": self._day(task_spec.get("actual_offset")),
                            "progress": Decimal(str(task_spec.get("progress", 0))),
                            "weight": Decimal(str(task_spec.get("weight", 1))),
                            "assignee": assignee,
                            "created_by": project.created_by,
                        },
                    )
                    created_tasks += int(task_created)
                    tasks_by_title[task_spec["title"]] = task

            # Dépendances posées après création (M2M), sans cycle.
            for spec in milestones:
                for task_spec in spec["tasks"]:
                    task = tasks_by_title.get(task_spec["title"])
                    for dependency_title in task_spec.get("depends_on", []):
                        dependency = tasks_by_title.get(dependency_title)
                        if task is not None and dependency is not None:
                            task.depends_on.add(dependency)

            recalculate_project_progress(project)

        return created_milestones, created_tasks

    @transaction.atomic
    def _seed_evidences(self, projects_by_code: dict) -> int:
        """Preuves terrain de démonstration : photos générées en mémoire, jamais committées.

        Les images sont fabriquées par Pillow (motifs de couleur) : aucune donnée binaire dans
        le dépôt, et le seed reste rapide. Chaque preuve est rattachée à un membre qui possède
        réellement la capacité de capture sur le projet.
        """
        import io
        from decimal import Decimal

        from django.core.files.base import ContentFile
        from django.utils import timezone
        from PIL import Image

        from apps.evidences.models import Evidence, EvidenceStatus
        from apps.evidences.storage import sha256_of
        from apps.evidences.tasks import generate_evidence_derivatives
        from apps.projects.models import ProjectMember

        users_by_role = {user.role: user for user in User.objects.all()}
        created = 0

        for code, specs in EVIDENCES.items():
            project = projects_by_code.get(code)
            if project is None:
                continue
            for index, spec in enumerate(specs):
                author = users_by_role.get(spec["role"])
                if (
                    author is None
                    or not ProjectMember.objects.filter(
                        project=project, user=author, is_active=True
                    ).exists()
                ):
                    continue

                # Pas de doublon : la clé est le couple (projet, description).
                if Evidence.objects.filter(
                    project=project, description=spec["description"]
                ).exists():
                    continue

                width, height = 1280, 960
                image = Image.new("RGB", (width, height), spec["color"])
                for x in range(0, width, 80):
                    for y in range(0, height, 80):
                        image.putpixel((x, y), (250, 250, 240))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=80)
                payload = buffer.getvalue()

                captured_at = timezone.now() - timedelta(days=spec["days_ago"])
                latitude = spec.get("latitude")
                longitude = spec.get("longitude")
                evidence = Evidence(
                    project=project,
                    author=author,
                    captured_at=captured_at,
                    latitude=Decimal(str(latitude)) if latitude is not None else None,
                    longitude=Decimal(str(longitude)) if longitude is not None else None,
                    gps_accuracy=spec.get("gps_accuracy"),
                    gps_status="AVAILABLE" if latitude is not None else "UNAVAILABLE",
                    device_model=spec.get("device_model", "Tecno Spark 10"),
                    device_platform="Android 13",
                    app_version="0.5.0",
                    description=spec["description"],
                    hash_sha256=sha256_of(payload),
                    idempotency_key=f"seed-{code.lower()}-{index:02d}",
                    size_bytes=len(payload),
                    content_type="image/jpeg",
                    status=spec.get("status", EvidenceStatus.PENDING),
                    sync_status="SYNCED",
                )
                evidence.file.save(
                    f"seed-{code.lower()}-{index:02d}.jpg", ContentFile(payload), save=False
                )
                evidence.save()
                generate_evidence_derivatives(evidence.pk)
                created += 1

        return created

    def _seed_projects(self, with_finance: bool = True) -> tuple:
        from apps.organizations.models import Organization, OrganizationMember
        from apps.projects.models import Project, ProjectMember

        users_by_role = {user.role: user for user in User.objects.all()}

        def user_for(role: str):
            return users_by_role.get(role) or User.objects.filter(role=role).first()

        organizations: dict[str, Organization] = {}
        created_organizations = 0
        for spec in ORGANIZATIONS:
            owner = user_for(spec["owner_role"]) or next(iter(users_by_role.values()))
            organization, created = Organization.objects.get_or_create(
                slug=_slugify(spec["name"]),
                defaults={
                    "name": spec["name"],
                    "type": spec["type"],
                    "city": spec["city"],
                    "owner": owner,
                },
            )
            created_organizations += int(created)
            OrganizationMember.objects.get_or_create(
                organization=organization,
                user=owner,
                defaults={"role": "ORG_OWNER"},
            )
            organizations[spec["key"]] = organization

        created_projects = 0
        created_memberships = 0
        projects_by_code: dict = {}
        for spec in PROJECTS:
            organization = organizations[spec["organization"]]
            creator = user_for("PROJECT_OWNER") or organization.owner
            project, created = Project.objects.get_or_create(
                organization=organization,
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "city": spec["city"],
                    "region": spec["region"],
                    "location_label": spec["location_label"],
                    "latitude": spec["latitude"],
                    "longitude": spec["longitude"],
                    "budget_total": spec["budget_total"],
                    "status": spec["status"],
                    "planned_start_date": spec["planned_start_date"],
                    "planned_end_date": spec["planned_end_date"],
                    "created_by": creator,
                },
            )
            created_projects += int(created)
            projects_by_code[spec["code"]] = project

            for role, flags in spec["members"].items():
                member_user = user_for(role)
                if member_user is None:
                    continue
                _, membership_created = ProjectMember.objects.get_or_create(
                    project=project,
                    user=member_user,
                    defaults={"role": role, **flags},
                )
                created_memberships += int(membership_created)

        created_milestones, created_tasks = self._seed_planning(projects_by_code)
        created_evidences = self._seed_evidences(projects_by_code)
        finance = (
            self._seed_finance(projects_by_code)
            if with_finance
            else {"lines": 0, "expenses": 0, "payments": 0, "receipts": 0}
        )

        return (
            created_organizations,
            created_projects,
            created_memberships,
            created_milestones,
            created_tasks,
            created_evidences,
            finance,
        )
