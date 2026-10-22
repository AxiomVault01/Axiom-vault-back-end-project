from rest_framework.permissions import BasePermission

SUPER_ADMIN_ONLY_MESSAGE = "Only the super admin can manage the team."
PASSWORD_CHANGE_REQUIRED_MESSAGE = "Change your temporary password before continuing."
ORGANISATION_SUSPENDED_MESSAGE = "Your organisation's access is suspended. Contact AxiomVault support."


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


NOT_ADMIN_MESSAGE = "This account can't sign in to the Admin Control Panel."


class IsAxiomVaultStaff(BasePermission):
    """Admin Control Panel endpoints: AxiomVault staff (`is_staff`) only; the owner is staff too."""

    message = {"error": NOT_ADMIN_MESSAGE, "code": "not_admin"}

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


OWNER_ONLY_MESSAGE = "Only the platform owner can manage staff accounts."


class IsPlatformOwner(BasePermission):
    """Staff management: the platform owner (Django `is_superuser`) only (decided 2026-10-20)."""

    message = {"error": OWNER_ONLY_MESSAGE, "code": "owner_only"}

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)
