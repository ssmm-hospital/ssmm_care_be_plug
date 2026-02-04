from rest_framework.exceptions import ValidationError

from care.emr.models import ChargeItem
from care.emr.resources.charge_item.spec import (
    ChargeItemResourceOptions,
    ChargeItemStatusOptions,
)
from care.security.authorization import AuthorizationController
from care.security.authorization.service_request import ServiceRequetAuthorizerUtility
from care.security.permissions.diagnostic_report import DiagnosticReportPermissions


class SSMMDiagnosticReportAuthorizer(ServiceRequetAuthorizerUtility):


    def can_read_diagnostic_report(self, user, service_request):
        if user.is_superuser:
            return True

        charge_items = ChargeItem.objects.filter(service_resource=ChargeItemResourceOptions.service_request.value, service_resource_id=str(service_request.external_id) , status__in=[ChargeItemStatusOptions.billable.value, ChargeItemStatusOptions.billed.value]).exists()
        if charge_items:
            raise ValidationError("Diagnostic report is not billed")

        return self.has_permission_on_service_request(
            user,
            service_request,
            DiagnosticReportPermissions.can_read_diagnostic_report.name,
        )


AuthorizationController.register_override_controller(SSMMDiagnosticReportAuthorizer)
