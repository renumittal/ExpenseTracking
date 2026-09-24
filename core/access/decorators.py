"""
Enforcement helpers built on top of access.services.has_perm -- no view should call
core.models.RolePermission / UserAccess / AccessOverride directly; everything goes through here so
there is exactly one place that decides "can this user hit this endpoint" (services.py).

    @require_perm('canEditExpense')                      # function-based view / plain function
    class FooView(RequirePermMixin, APIView):
        required_perm = 'canDeleteExpense'
        project_lookup = 'pk'                             # (default) view.kwargs[...] is a Project id

`project_lookup` can also be a callable `f(view, request) -> Project|int|None` for views where the
project isn't the URL's own pk (e.g. it hangs off a related object).
"""
import functools

from rest_framework.exceptions import PermissionDenied

from ..models import Project
from . import services


def _resolve_project(view, request, project_lookup):
    if project_lookup is None:
        return None
    if callable(project_lookup):
        return project_lookup(view, request)
    value = view.kwargs.get(project_lookup)
    if value is None:
        return None
    if isinstance(value, Project):
        return value
    return Project.objects.filter(pk=value).first()


def require_perm(code, project_lookup='pk'):
    """Decorator for a DRF APIView method (or any `(self, request, *a, **kw)` callable)."""
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(self, request, *args, **kwargs):
            self.kwargs = getattr(self, 'kwargs', kwargs)
            project = _resolve_project(self, request, project_lookup)
            if not services.has_perm(request.user, code, project):
                raise PermissionDenied('You do not have permission to do this.')
            return view_func(self, request, *args, **kwargs)
        return wrapper
    return decorator


class RequirePermMixin:
    """
    Mixin for a DRF `APIView`/`ViewSet`: set `required_perm` (a code, or a dict {method: code}) and
    optionally `project_lookup` (see module docstring). Runs on every request before the handler.
    """
    required_perm = None
    project_lookup = 'pk'

    def check_permissions(self, request):
        super().check_permissions(request)
        code = self.required_perm
        if isinstance(code, dict):
            code = code.get(request.method)
        if code is None:
            return
        project = _resolve_project(self, request, self.project_lookup)
        if not services.has_perm(request.user, code, project):
            self.permission_denied(request, message='You do not have permission to do this.')
