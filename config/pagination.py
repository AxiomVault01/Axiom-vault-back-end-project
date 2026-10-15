from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Every list in the API: 20 items a page by default, `?page=2`, `?page_size=` up to 100.

    The body is {"count", "next", "previous", "results"}; a page past the end is 404.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
