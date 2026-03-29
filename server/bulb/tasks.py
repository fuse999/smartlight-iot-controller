from celery import shared_task
from django.utils import timezone

from .models import LightSchedule
from .services import apply_schedule

import logging
logger = logging.getLogger(__name__)

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def run_schedule(self, schedule_id: int) -> None:
    sched = LightSchedule.objects.filter(
        id=schedule_id,
        enabled=True,
        claimed_at__isnull=False,
    ).first()

    if not sched:
        return

    apply_schedule(sched)


@shared_task
def enqueue_due_schedules() -> None:
    now = timezone.now()
    logger.info("enqueue_due_schedules fired at %s", now)

    candidate_ids = list(
        LightSchedule.objects
        .filter(
            enabled=True,
            claimed_at__isnull=True,
            next_run_at__isnull=False,
            next_run_at__lte=now,
        )
        .order_by("next_run_at")
        .values_list("id", flat=True)[:500]
    )

    logger.info("Found %s due schedules", len(candidate_ids))

    for schedule_id in candidate_ids:
        claimed = LightSchedule.objects.filter(
            id=schedule_id,
            claimed_at__isnull=True,
            enabled=True,
        ).update(claimed_at=now)

        if claimed == 1:
            logger.info("Queueing run_schedule for schedule %s", schedule_id)
            run_schedule.delay(schedule_id)