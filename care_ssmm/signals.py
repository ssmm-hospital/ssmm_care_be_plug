from decimal import Decimal

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils.timezone import now
from rest_framework.exceptions import ValidationError
from care.emr.models import (
    ChargeItem,
    ChargeItemDefinition,
    ResourceCategory,
    Encounter,
    TokenBooking,
)
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.charge_item.apply_charge_item_definition import (
    apply_charge_item_definition,
)
from care.emr.resources.charge_item.spec import ChargeItemResourceOptions,ChargeItemStatusOptions
from care.emr.resources.payment_reconciliation.spec import PaymentReconciliationPaymentMethodOptions, PaymentReconciliationTypeOptions

SYSTEM_REGISTRATION_FEE_CHARGE_ITEM_DEFINITION_SLUG = "i-system:registration-fee"
REGISTRATION_RESOURCE_CATEGORY_SLUG_VALUE = "registration"
PRICE_COMPONENTS = [{"amount": 50, "monetary_component_type": "base"}]
DIFF_DAYS = 180
CASH_PAYMENT_LIMIT = Decimal("200000")
CASH_CREDIT_NOTE_LIMIT = Decimal("10000")


@receiver(post_save, sender=TokenBooking)
def handle_registration_fee(sender, instance, **kwargs):
    patient = instance.patient
    facility = instance.token_slot.resource.facility
    registration_resource_category_slug = (
        f"f-{facility.external_id}-{REGISTRATION_RESOURCE_CATEGORY_SLUG_VALUE}"
    )
    resource_category = ResourceCategory.objects.filter(
        slug=registration_resource_category_slug,
    ).first()

    if not resource_category:
        resource_category = ResourceCategory.objects.create(
            slug=registration_resource_category_slug,
            facility=facility,
            title="Registration",
            description="Registration",
            resource_type="charge_item_definition",
            resource_sub_type="all:other",
        )
    charge_item_definition = ChargeItemDefinition.objects.filter(
        slug=SYSTEM_REGISTRATION_FEE_CHARGE_ITEM_DEFINITION_SLUG,
        facility=facility,
    ).first()

    if not charge_item_definition:
        charge_item_definition = ChargeItemDefinition.objects.create(
            slug=SYSTEM_REGISTRATION_FEE_CHARGE_ITEM_DEFINITION_SLUG,
            facility=facility,
            title="Registration Fee",
            description="Registration Fee",
            price_components=PRICE_COMPONENTS,
            status="active",
            category=resource_category,
        )
    elif (
        not charge_item_definition.category
        or charge_item_definition.category != resource_category
    ):
        charge_item_definition.category = resource_category
        charge_item_definition.save()

    charge_item = (
        ChargeItem.objects.filter(
            patient=patient,
            facility=facility,
            charge_item_definition=charge_item_definition,
        )
        .order_by("-created_date")
        .first()
    )
    diff_days = -1 if not charge_item else (now() - charge_item.created_date).days
    if diff_days == -1 or diff_days > DIFF_DAYS:
        charge_item = apply_charge_item_definition(
            charge_item_definition,
            patient,
            facility,
            quantity=1,
        )
        charge_item.service_resource = ChargeItemResourceOptions.appointment.value
        charge_item.service_resource_id = str(instance.external_id)
        charge_item.save()

@receiver(pre_save, sender=Encounter)
def disallow_encounter_unpaid(sender, instance, **kwargs):
    if instance and instance.appointment:
        charge_items = ChargeItem.objects.filter(service_resource=ChargeItemResourceOptions.appointment.value, service_resource_id=str(instance.appointment.external_id), status__in=[ChargeItemStatusOptions.billable.value, ChargeItemStatusOptions.billed.value]).exists()
        if charge_items:
            raise ValidationError("Appointment charge item must be paid before encounter can be created")
        


@receiver(pre_save, sender=TokenBooking)
def check_patient_ip_exists(sender, instance,*args, **kwargs):
    from datetime import timedelta, datetime
    from care.utils.time_util import care_now
    from care.emr.resources.charge_item.sync_charge_item_costs import (
        sync_charge_item_costs,
    )
    from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
    if instance.id:
        old_instance = TokenBooking.objects.only("charge_item_id").get(id=instance.id)
        if old_instance.charge_item_id:
            return
    if not instance.charge_item_id:
        return
    if not instance.token_slot.resource.user:
        return
    booking_date = instance.token_slot.start_datetime
    patient = instance.patient
    encounters = Encounter.objects.filter(
        status__in=[StatusChoices.completed.value, StatusChoices.discharged.value],
        patient=patient,
        encounter_class=ClassChoices.imp.value,
    ).order_by("-created_date")
    for encounter in encounters:
        if encounter.period and encounter.period["end"] and encounter.care_team:
            care_team_user_id = encounter.care_team[0].get("user_id" , -1)
            end_date = datetime.fromisoformat(encounter.period["end"])
            if int(care_team_user_id) != instance.token_slot.resource.user.id:
                continue
            if end_date > (booking_date - timedelta(days=10)):
                charge_item = instance.charge_item
                charge_item.quantity = 0
                sync_charge_item_costs(charge_item)
                charge_item.save()
                # Make the charge item 0 and sync again
                return


CARD_METHODS = {
    PaymentReconciliationPaymentMethodOptions.ccca.value,
    PaymentReconciliationPaymentMethodOptions.debc.value,
}
DIRECT_DEPOSIT_METHODS = {
    PaymentReconciliationPaymentMethodOptions.ddpo.value,
}


@receiver(pre_save, sender=PaymentReconciliation)
def validate_reference_number(sender, instance, **kwargs):
    method = instance.method
    ref = instance.reference_number

    if method in CARD_METHODS:
        if not ref or len(ref) != 4:
            raise ValidationError(
                "Reference number must be exactly 4 characters for card payments"
            )
    elif method in DIRECT_DEPOSIT_METHODS:
        if not ref or len(ref) != 5:
            raise ValidationError(
                "Reference number must be exactly 5 characters for direct deposit payments"
            )


@receiver(pre_save, sender=PaymentReconciliation)
def block_cash_payment_above_limit(sender, instance, **kwargs):
    if instance.method == PaymentReconciliationPaymentMethodOptions.cash.value and Decimal(instance.amount) > CASH_PAYMENT_LIMIT:
        raise ValidationError(f"Cash payments above {CASH_PAYMENT_LIMIT} are not allowed")


@receiver(pre_save, sender=PaymentReconciliation)
def block_cash_refund_above_limit(sender, instance, **kwargs):
    if (
        instance.method == PaymentReconciliationPaymentMethodOptions.cash.value
        and instance.is_credit_note
        and instance.reconciliation_type != PaymentReconciliationTypeOptions.adjustment.value
        and Decimal(instance.amount) > CASH_CREDIT_NOTE_LIMIT
    ):
        raise ValidationError(
            f"Cash refunds above {CASH_CREDIT_NOTE_LIMIT} are not allowed"
        )
    
@receiver(pre_save, sender=PaymentReconciliation)
def block_payment_transfer_above_total_paid(sender, instance, **kwargs):
    if (
        instance.reconciliation_type == PaymentReconciliationTypeOptions.adjustment.value
        and instance.is_credit_note
        and instance.account.total_paid < Decimal(instance.amount)
    ):
        raise ValidationError(
            f"Insufficient funds for payment transfer"
        )