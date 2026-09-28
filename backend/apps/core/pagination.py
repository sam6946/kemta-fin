from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Pagination de toutes les collections : 20 par défaut, 100 au maximum."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
