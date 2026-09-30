import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_new_user_is_unverified_and_password_is_hashed():
    user = User.objects.create_user(
        username="ada",
        email="ada@example.com",
        password="Str0ng-Passw0rd!",
        full_name="Ada Obi",
        organization="Axiom",
        department="operations",
        role="fraud_analyst",
    )

    assert user.is_verified is False
    assert user.password != "Str0ng-Passw0rd!"
    assert user.check_password("Str0ng-Passw0rd!")
