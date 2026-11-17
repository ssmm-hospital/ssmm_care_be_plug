import logging

from care.emr.api.viewsets.device import disassociate_device_from_encounter
from care.emr.api.viewsets.location import close_related_location_from_encounter
from care.emr.models import Account, Encounter
from care.emr.resources.account.spec import (
    AccountBillingStatusOptions,
    AccountStatusOptions,
)
from care.emr.resources.encounter.constants import (
    COMPLETED_CHOICES,
    ClassChoices,
    StatusChoices,
)
from care.utils.time_util import care_now
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def _open_accounts(encounter_id, patient_id, facility_id):
    # Care also uses the patient's active open account when no account is linked.
    return Account.objects.filter(
        billing_status=AccountBillingStatusOptions.open.value,
    ).filter(
        Q(primary_encounter_id=encounter_id)
        | Q(
            patient_id=patient_id,
            facility_id=facility_id,
            status=AccountStatusOptions.active.value,
        )
    )


def _close_encounter(encounter_id, *, created_before):
    """Recheck eligibility and complete one encounter atomically."""
    with transaction.atomic():
        encounter = (
            Encounter.objects.select_for_update()
            .filter(pk=encounter_id, created_date__lt=created_before)
            .first()
        )
        if encounter is None or encounter.status in COMPLETED_CHOICES:
            return None

        is_ip = encounter.encounter_class == ClassChoices.imp.value
        if is_ip:
            care_completed = Account.objects.filter(
                primary_encounter=encounter,
                billing_status=AccountBillingStatusOptions.carecomplete_notbilled.value,
            ).exists()
            if (
                not care_completed
                or _open_accounts(
                    encounter.pk, encounter.patient_id, encounter.facility_id
                ).exists()
            ):
                return None
        elif encounter.encounter_class != ClassChoices.amb.value:
            return None

        completed_at = str(care_now())
        encounter.status = StatusChoices.completed.value
        history = encounter.status_history or {}
        history["history"] = (history.get("history") or []) + [
            {"status": StatusChoices.completed.value, "moved_at": completed_at}
        ]
        encounter.status_history = history
        period = encounter.period or {}
        if not period.get("end"):
            period["end"] = completed_at
        encounter.period = period
        encounter.save(
            update_fields=["status", "status_history", "period", "modified_date"]
        )
        disassociate_device_from_encounter(encounter)
        close_related_location_from_encounter(encounter)

        return (0, 1) if is_ip else (1, 0)


def auto_close_encounters():
    """Close eligible OP and IP encounters created before today's local midnight."""
    created_before = timezone.localtime(
        care_now(), timezone.get_default_timezone()
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    linked_care_completed = Account.objects.filter(
        primary_encounter_id=OuterRef("pk"),
        billing_status=AccountBillingStatusOptions.carecomplete_notbilled.value,
    )
    candidates = (
        Encounter.objects.filter(created_date__lt=created_before)
        .exclude(status__in=COMPLETED_CHOICES)
        .annotate(
            has_care_completed_account=Exists(linked_care_completed),
            has_open_account=Exists(
                _open_accounts(
                    OuterRef("pk"), OuterRef("patient_id"), OuterRef("facility_id")
                )
            ),
        )
        .filter(
            Q(encounter_class=ClassChoices.amb.value)
            | Q(
                encounter_class=ClassChoices.imp.value,
                has_care_completed_account=True,
                has_open_account=False,
            )
        )
        .order_by("pk")
    )
    counts = {"op_closed": 0, "ip_closed": 0}
    failures = 0
    for encounter_id in candidates.values_list("pk", flat=True).iterator(
        chunk_size=500
    ):
        try:
            result = _close_encounter(encounter_id, created_before=created_before)
        except Exception:
            logger.exception("Unable to auto-close encounter %s", encounter_id)
            failures += 1
            continue
        if result is not None:
            counts["op_closed"] += result[0]
            counts["ip_closed"] += result[1]

    logger.info("Encounter auto-close results: %s; failures: %s", counts, failures)
    if failures:
        raise RuntimeError(f"Failed to auto-close {failures} encounter(s)")
    return counts
