from celery import current_app, shared_task
from celery.schedules import crontab

from care_ssmm.auto_close import auto_close_encounters


@shared_task
def auto_close_encounters_task():
    """Run the daily OP and eligible IP encounter cleanup."""
    return auto_close_encounters()


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        crontab(minute="0", hour="4"),
        auto_close_encounters_task.s(),
        name="auto_close_encounters",
    )
