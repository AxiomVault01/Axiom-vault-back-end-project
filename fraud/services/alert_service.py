import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import Alert, AlertAuditLog


class AlertService:

    # -------------------------
    # Create Alert
    # -------------------------
    @staticmethod
    def create_alert(payroll_id, risk_score, severity, created_by="system"):
        """
        Creates a new alert when fraud risk exceeds threshold.
        """

        with transaction.atomic():

            alert = Alert.objects.create(
                id=uuid.uuid4(),
                payroll_id=payroll_id,
                risk_score=risk_score,
                severity=severity,
                status="OPEN"
            )

            AlertAuditLog.objects.create(
                alert=alert,
                action="CREATED",
                new_status="OPEN",
                performed_by=created_by
            )

        return alert


    # -------------------------
    # Change Alert Status
    # -------------------------
    @staticmethod
    def change_status(alert, new_status, user):
        """
        Change alert status and record audit log.
        """

        valid_status = ["OPEN", "IN_REVIEW", "CLOSED"]

        if new_status not in valid_status:
            raise ValidationError("Invalid status value")

        previous_status = alert.status

        if previous_status == new_status:
            return alert

        with transaction.atomic():

            alert.status = new_status
            alert.save()

            AlertAuditLog.objects.create(
                alert=alert,
                action="STATUS_CHANGED",
                previous_status=previous_status,
                new_status=new_status,
                performed_by=user
            )

        return alert


    # -------------------------
    # Add Comment to Alert
    # -------------------------
    @staticmethod
    def add_comment(alert, user):
        """
        Example extension point for adding investigation comments.
        """

        AlertAuditLog.objects.create(
            alert=alert,
            action="COMMENT_ADDED",
            performed_by=user
        )


    # -------------------------
    # Get Alert Audit Logs
    # -------------------------
    @staticmethod
    def get_audit_logs(alert):
        """
        Retrieve audit history for an alert.
        """

        return alert.audit_logs.all()