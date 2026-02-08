from django.core.management.base import BaseCommand
from django.utils import timezone
from bulb.models import LightSchedule
from bulb.services import apply_schedule

class Command(BaseCommand):
    help = "Execute due LightSchedule rows"

    def handle(self, *args, **options):
        now = timezone.now()

        due = (LightSchedule.objects
               .filter(enabled=True, executed_at__isnull=True, run_at__lte=now)
               .order_by("run_at"))

        count = 0
        for sched in due:
            apply_schedule(sched)
            count += 1

        self.stdout.write(f"Executed {count} schedules")
