from django.http import JsonResponse


class HealthCheckMiddleware:
    """
    Answers /health/ before Django's host check.

    Render's health checker calls the server by its internal IP address, which is not (and
    cannot be) in ALLOWED_HOSTS, so it would get a 400. Only this one path skips the check:
    no login, no database, no data. Every other URL still needs a valid Host.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/health/':
            return JsonResponse({'status': 'ok'})
        return self.get_response(request)
