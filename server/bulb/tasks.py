from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import LightSchedule
from .services import apply_schedule

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def run_schedule(self, schedule_id: int) -> None:
    sched = LightSchedule.objects.filter(
        id=schedule_id,
        enabled=True,
        executed_at__isnull=True,
    ).first()

    if not sched:
        return

    apply_schedule(sched)


@shared_task
def enqueue_due_schedules() -> None:
    now = timezone.now()

    candidate_ids = list(
        LightSchedule.objects
        .filter(
            enabled=True,
            executed_at__isnull=True,
            claimed_at__isnull=True,
            run_at__lte=now,
        )
        .order_by("run_at")
        .values_list("id", flat=True)[:500]
    )

    for schedule_id in candidate_ids:
        claimed = LightSchedule.objects.filter(
            id=schedule_id,
            claimed_at__isnull=True,
            executed_at__isnull=True,
            enabled=True,
        ).update(claimed_at=now)

        if claimed == 1:
            run_schedule.delay(schedule_id)