"""Dashboard and Analytics screens: KPI cards, the risk heatmap, risk trends, and the investigation queue."""

from datetime import timedelta

from django.db.models import Case, Count, IntegerField, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import PASSWORD_CHANGE_REQUIRED_MESSAGE, PasswordChangeNotRequired
from ingestion.viewsets import NO_ORGANISATION_MESSAGE, BelongsToOrganisation

from .models import Alert
from .models import Case as InvestigationCase  # Django also has a `Case` (used below).
from .serializers import AlertListSerializer

OPEN_STATUSES = ["new", "investigating", "escalated"]
INVESTIGATION_STATUSES = ["investigating", "escalated"]
SEVERITIES = ["critical", "high", "medium", "low"]
STATUSES = [value for value, _ in Alert.STATUS_CHOICES]
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
ALLOWED_DAYS = [10, 15, 20, 25, 30]
HEATMAP_DAYS = {"weekly": 7, "monthly": 30}
TREND_DAYS = 30
UNASSIGNED = "Unassigned"

AUTH_NOTE = (
    "**Authentication:** `Authorization: Bearer <access_token>` of any member of the organisation. Every number "
    "counts only the caller's organisation."
)
ERRORS = {
    401: OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        examples=[OpenApiExample("No token", value={"detail": "Authentication credentials were not provided."})],
    ),
    403: OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        examples=[
            OpenApiExample("Temporary password", value={"error": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": "password_change_required"}),
            OpenApiExample("No organisation", value={"error": NO_ORGANISATION_MESSAGE}),
        ],
    ),
}


def exposure_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def risk_exposure(alerts) -> dict:
    """Severity-weighted average risk score of open alerts; `source` says who defined it (ML later)."""
    weighted = total_weight = 0
    for severity, score in alerts.filter(status__in=OPEN_STATUSES).values_list("severity", "risk_score"):
        weight = SEVERITY_WEIGHT[severity]
        weighted += weight * score
        total_weight += weight
    score = round(weighted / total_weight) if total_weight else 0
    return {"score": score, "level": exposure_level(score), "source": "derived"}


class DaysSerializer(serializers.Serializer):
    days = serializers.ChoiceField(choices=ALLOWED_DAYS, default=30, help_text="Period in days: 10, 15, 20, 25, or 30.")


class PeriodSerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=list(HEATMAP_DAYS), default="monthly", help_text="`weekly` (7 days) or `monthly` (30 days).")


SummarySchema = inline_serializer("DashboardSummary", {
    "period_days": serializers.IntegerField(help_text="The period the numbers cover, in days."),
    "critical_alerts": serializers.IntegerField(help_text="Open alerts with severity `critical` (Critical Alerts card)."),
    "open_investigations": serializers.IntegerField(help_text="Alerts being investigated or escalated (Open Investigations card)."),
    "active_cases": serializers.IntegerField(help_text="Cases opened in the period and not closed (Active Cases card)."),
    "risk_exposure": inline_serializer("RiskExposure", {
        "score": serializers.IntegerField(help_text="0-100: severity-weighted average risk score of open alerts."),
        "level": serializers.ChoiceField(choices=["low", "medium", "high", "critical"], help_text="Band of the score: <25 low, <50 medium, <75 high, else critical."),
        "source": serializers.ChoiceField(choices=["derived", "ml"], help_text="`derived` (our formula) until ML defines the score."),
    }),
    "alerts_by_severity": serializers.DictField(child=serializers.IntegerField(), help_text="Alerts in the period per severity; every severity is present."),
    "alerts_by_status": serializers.DictField(child=serializers.IntegerField(), help_text="Alerts in the period per status; every status is present."),
})
HeatmapSchema = inline_serializer("RiskHeatmap", {
    "period": serializers.CharField(help_text="`weekly` or `monthly`."),
    "departments": serializers.ListField(child=serializers.CharField(), help_text="Row labels, sorted; a blank department is `Unassigned`."),
    "risk_types": serializers.ListField(child=serializers.CharField(), help_text="Column labels, sorted."),
    "cells": serializers.ListField(child=inline_serializer("HeatmapCell", {
        "department": serializers.CharField(), "risk_type": serializers.CharField(), "count": serializers.IntegerField(),
    }), help_text="Only cells with at least one alert; any missing cell is 0."),
})
TrendsSchema = inline_serializer("RiskTrends", {
    "days": serializers.ListField(child=inline_serializer("TrendDay", {
        "date": serializers.DateField(), "critical": serializers.IntegerField(), "high": serializers.IntegerField(),
        "medium": serializers.IntegerField(), "low": serializers.IntegerField(),
    }), help_text="30 entries, oldest first, ending today (UTC); days without alerts are zeros."),
})


class DashboardViewSet(viewsets.GenericViewSet):
    """Dashboard and Analytics screens."""

    permission_classes = [IsAuthenticated, PasswordChangeNotRequired, BelongsToOrganisation]
    serializer_class = AlertListSerializer
    queryset = Alert.objects.none()  # Only tells Swagger the model; requests use alerts().

    def alerts(self):
        return Alert.objects.filter(organisation=self.request.user.organisation)

    @staticmethod
    def since(days):
        return timezone.now() - timedelta(days=days)

    @extend_schema(
        tags=["Dashboard"],
        summary="Dashboard KPI cards",
        description=(
            "**Dashboard screen.** The four KPI cards for the chosen period (the Select Days menu), plus counts by "
            "severity and status.\n\n"
            "- **Critical Alerts:** open (`new`, `investigating`, `escalated`) alerts with severity `critical`.\n"
            "- **Open Investigations:** alerts with status `investigating` or `escalated`.\n"
            "- **Active Cases:** cases opened in the period that are not closed (any status but `closed`).\n"
            "- **Risk Exposure Score:** `score` 0-100 is the severity-weighted average risk score of open alerts "
            "(weights critical 4, high 3, medium 2, low 1); 0 when there are none. `level` is its band, for the "
            "\"High risk level\" text. `source` is `derived` until ML defines the score.\n\n"
            "Only alerts detected in the last `days` days count. `days` is 10, 15, 20, 25, or 30 (default 30); "
            "anything else is 400.\n\n" + AUTH_NOTE
        ),
        parameters=[OpenApiParameter("days", OpenApiTypes.INT, description="10, 15, 20, 25, or 30. Default 30.")],
        responses={
            200: OpenApiResponse(response=SummarySchema, examples=[OpenApiExample("Summary", value={
                "period_days": 30, "critical_alerts": 3, "open_investigations": 5, "active_cases": 2,
                "risk_exposure": {"score": 72, "level": "high", "source": "derived"},
                "alerts_by_severity": {"critical": 3, "high": 6, "medium": 0, "low": 0},
                "alerts_by_status": {"new": 4, "investigating": 3, "escalated": 2, "false_positive": 1},
            })]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Bad period", value={"days": ['"7" is not a valid choice.']})]),
            **ERRORS,
        },
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        params = DaysSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        days = params.validated_data["days"]
        alerts = self.alerts().filter(detected_at__gte=self.since(days))
        by_severity = dict(alerts.values_list("severity").annotate(n=Count("id")))
        by_status = dict(alerts.values_list("status").annotate(n=Count("id")))
        return Response({
            "period_days": days,
            "critical_alerts": alerts.filter(status__in=OPEN_STATUSES, severity="critical").count(),
            "open_investigations": alerts.filter(status__in=INVESTIGATION_STATUSES).count(),
            "active_cases": InvestigationCase.objects.filter(
                organisation=request.user.organisation, created_at__gte=self.since(days)
            ).exclude(status__in=InvestigationCase.DONE_STATUSES).count(),
            "risk_exposure": risk_exposure(alerts),
            "alerts_by_severity": {s: by_severity.get(s, 0) for s in SEVERITIES},
            "alerts_by_status": {s: by_status.get(s, 0) for s in STATUSES},
        })

    @extend_schema(
        tags=["Dashboard"],
        summary="Risk distribution heatmap",
        description=(
            "**Analytics screen, Risk Distribution Heatmap.** Alert counts by department (rows) and risk type "
            "(columns). `weekly` covers the last 7 days, `monthly` (default) the last 30. Only non-zero cells are "
            "returned; draw every other cell as 0. Colour the cells with the severity legend as the frontend sees "
            "fit; the counts are the data.\n\n" + AUTH_NOTE
        ),
        parameters=[OpenApiParameter("period", OpenApiTypes.STR, description="`weekly` or `monthly`. Default `monthly`.")],
        responses={
            200: OpenApiResponse(response=HeatmapSchema, examples=[OpenApiExample("Heatmap", value={
                "period": "monthly",
                "departments": ["Finance", "Procurement", "Unassigned"],
                "risk_types": ["Duplicate Bank Account", "Duplicate Payment"],
                "cells": [
                    {"department": "Finance", "risk_type": "Duplicate Bank Account", "count": 2},
                    {"department": "Procurement", "risk_type": "Duplicate Payment", "count": 4},
                    {"department": "Unassigned", "risk_type": "Duplicate Payment", "count": 1},
                ],
            })]),
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample("Bad period", value={"period": ['"yearly" is not a valid choice.']})]),
            **ERRORS,
        },
    )
    @action(detail=False, methods=["get"])
    def heatmap(self, request):
        params = PeriodSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        period = params.validated_data["period"]
        rows = (
            self.alerts().filter(detected_at__gte=self.since(HEATMAP_DAYS[period]))
            .values_list("department", "risk_type").annotate(n=Count("id"))
        )
        counts = {}
        for department, risk_type, n in rows:
            key = (department or UNASSIGNED, risk_type)
            counts[key] = counts.get(key, 0) + n
        cells = [{"department": d, "risk_type": r, "count": n} for (d, r), n in sorted(counts.items())]
        return Response({
            "period": period,
            "departments": sorted({d for d, _ in counts}),
            "risk_types": sorted({r for _, r in counts}),
            "cells": cells,
        })

    @extend_schema(
        tags=["Dashboard"],
        summary="Risk trends, last 30 days",
        description=(
            "**Analytics screen, Risk Trends (Last 30 Days).** New alerts per day by severity, for the 30 days "
            "ending today (UTC), oldest first. Every day is present; days without alerts are zeros, so the chart "
            "has no gaps.\n\n" + AUTH_NOTE
        ),
        responses={
            200: OpenApiResponse(response=TrendsSchema, examples=[OpenApiExample("Trends", value={"days": [
                {"date": "2026-10-14", "critical": 0, "high": 2, "medium": 0, "low": 0},
                {"date": "2026-10-15", "critical": 1, "high": 0, "medium": 0, "low": 0},
            ]})]),
            **ERRORS,
        },
    )
    @action(detail=False, methods=["get"])
    def trends(self, request):
        today = timezone.now().date()
        first = today - timedelta(days=TREND_DAYS - 1)
        rows = (
            self.alerts().filter(detected_at__date__gte=first)
            .annotate(day=TruncDate("detected_at")).values_list("day", "severity").annotate(n=Count("id"))
        )
        counts = {(day, severity): n for day, severity, n in rows}
        days = []
        for offset in range(TREND_DAYS):
            day = first + timedelta(days=offset)
            days.append({"date": day.isoformat(), **{s: counts.get((day, s), 0) for s in SEVERITIES}})
        return Response({"days": days})

    @extend_schema(
        tags=["Dashboard"],
        summary="Investigation queue",
        description=(
            "**Analytics screen, Investigation Queue.** Alerts being investigated or escalated, most severe first, "
            "then newest. Each item has the same fields as the alerts list; open one with "
            "`GET /api/v1/fraud/alerts/{reference}/`.\n\n**Paginated:** 20 a page by default; `?page=`, "
            "`?page_size=` up to 100; `{count, next, previous, results}`.\n\n" + AUTH_NOTE
        ),
        parameters=[
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number, from 1."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Items per page, default 20, at most 100."),
        ],
        responses={200: AlertListSerializer(many=True), **ERRORS},
    )
    @action(detail=False, methods=["get"], url_path="investigation-queue")
    def investigation_queue(self, request):
        rank = Case(*[When(severity=s, then=Value(i)) for i, s in enumerate(SEVERITIES)], output_field=IntegerField())
        queue = (
            self.alerts().filter(status__in=INVESTIGATION_STATUSES).select_related("assigned_reviewer")
            .annotate(rank=rank).order_by("rank", "-detected_at", "-number")
        )
        page = self.paginate_queryset(queue)
        return self.get_paginated_response(AlertListSerializer(page, many=True).data)
