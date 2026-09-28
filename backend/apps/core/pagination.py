from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Pagination de toutes les collections : 20 par défaut, 100 au maximum."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class LargeListPagination(PageNumberPagination):
    """Pagination des collections métier consommées « en entier » par l'interface (postes
    budgétaires, jalons, tâches, file de validation).

    100 éléments par page (200 au maximum) : un chantier ordinaire tient dans la première
    page, mais aucune collection n'est jamais renvoyée sans borne (phase 11).
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 200
