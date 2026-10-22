"""JWT authentication that also refuses users whose organisation is suspended (feature 20).

Checking here, where every request's token is read, covers every endpoint at once, including views
that set their own permission classes.
"""

from rest_framework import exceptions, status
from rest_framework_simplejwt.authentication import JWTAuthentication

from .permissions import ORGANISATION_SUSPENDED_MESSAGE


class OrganisationSuspendedError(exceptions.APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):
        super().__init__({"error": ORGANISATION_SUSPENDED_MESSAGE, "code": "organisation_suspended"})


class SuspensionAwareJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        organisation = user.organisation
        if organisation is not None and organisation.is_suspended:
            raise OrganisationSuspendedError()
        return user


# Swagger: describe this class exactly like simplejwt's (Bearer JWT, the lock icon on protected endpoints).
from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme  # noqa: E402


class SuspensionAwareJWTScheme(SimpleJWTScheme):
    target_class = "accounts.authentication.SuspensionAwareJWTAuthentication"
