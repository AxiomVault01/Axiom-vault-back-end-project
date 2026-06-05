# from django.contrib.auth import authenticate
# from rest_framework_simplejwt.tokens import RefreshToken
# from django.utils import timezone
# from datetime import timedelta

# from accounts.models import Session


# class AuthService:

#     @staticmethod
#     def login(email, password):

#         user = authenticate(username=email, password=password)

#         if not user:
#             return None, "Invalid credentials"

#         if not user.is_verified:
#             return None, "Account not verified"

#         refresh = RefreshToken.for_user(user)

#         Session.objects.create(
#             user=user,
#             token=str(refresh.access_token),
#             expires_at=timezone.now() + timedelta(minutes=30),
#         )

#         return {
#             "access": str(refresh.access_token),
#             "refresh": str(refresh),
#         }, None



from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken


class AuthService:

    @staticmethod
    def login(email, password):
        user = authenticate(username=email, password=password)

        if not user:
            return None, "Invalid credentials"

        if not user.is_verified:
            return None, "Account not verified"

        refresh = RefreshToken.for_user(user)

        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }, None

    @staticmethod
    def logout(refresh_token):
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            return True
        except Exception:
            return False