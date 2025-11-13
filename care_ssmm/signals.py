from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now

from care.emr.models import (
    ChargeItem,
    ChargeItemDefinition,
    ResourceCategory,
    TokenBooking,
)
from care.emr.resources.charge_item.apply_charge_item_definition import (
    apply_charge_item_definition,
)
from care.emr.resources.charge_item.spec import ChargeItemResourceOptions

SYSTEM_REGISTRATION_FEE_CHARGE_ITEM_DEFINITION_SLUG = "i-system:registration-fee"
REGISTRATION_RESOURCE_CATEGORY_SLUG_VALUE = "registration"
PRICE_COMPONENTS = [{"amount": 50, "monetary_component_type": "base"}]
DIFF_DAYS = 180


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
    charge_item_definition = None

    if not resource_category:
        resource_category = ResourceCategory.objects.create(
            slug=registration_resource_category_slug,
            facility=facility,
            title="Registration",
            description="Registration",
            resource_type="charge_item_definition",
            resource_sub_type="all:other",
        )
    else:
        charge_item_definition = ChargeItemDefinition.objects.filter(
            slug=SYSTEM_REGISTRATION_FEE_CHARGE_ITEM_DEFINITION_SLUG,
            facility=facility,
            category=resource_category,
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
