from rest_framework.permissions import BasePermission

SUPER_ADMIN_ONLY_MESSAGE = "Only the super admin can manage the team."
PASSWORD_CHANGE_REQUIRED_MESSAGE = "Change your temporary password before continuing."


class IsSuperAdmin(BasePermission):
    """Lets through only a logged-in organisation super admin."""

    message = {"error": SUPER_ADMIN_ONLY_MESSAGE}

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.account_type == "super_admin")


class PasswordChangeNotRequired(BasePermission):
    """Refuses a logged-in user who still has a temporary password; allows everyone else.

    The project default, so it applies everywhere except views that set their own permissions
    (change password and logout), which is exactly where such a user may still go.
    """

    message = {"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"}

    def has_permission(self, request, view):
        user = request.user
        return not (user and user.is_authenticated and user.must_change_password)
