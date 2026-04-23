from rest_framework.routers import DefaultRouter
from .viewsets import PayrollViewSet, BankAccountViewSet

router = DefaultRouter()
router.register("ingestion/payroll", PayrollViewSet, basename="payroll")
router.register("ingestion/bank-accounts", BankAccountViewSet, basename="bank-accounts")

urlpatterns = router.urls
