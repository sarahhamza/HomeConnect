"""appointments/management/commands/reassign_pending.py

- RDV PENDING attribués par l'IA et sans réponse depuis 2 h -> prestataire suivant.
- RDV SEARCHING (service IA injoignable à la création)     -> nouvelle tentative.

Les RDV réservés directement par le client (assigned_at vide) ne sont jamais touchés.

À lancer toutes les 10 min :
  - Windows : Planificateur de tâches -> python manage.py reassign_pending
  - Linux   : */10 * * * * cd /chemin/backend && python manage.py reassign_pending
  - ou Celery beat si tu l'utilises déjà.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.matching import assign_provider, release_and_reassign
from appointments.models import Appointment

REASSIGN_AFTER = timedelta(hours=2)


class Command(BaseCommand):
    help = "Réattribue les rendez-vous sans réponse du prestataire après 2 h."

    def handle(self, *args, **options):
        limit = timezone.now() - REASSIGN_AFTER
        seen, done = set(), 0

        stale = Appointment.objects.filter(
            status=Appointment.Status.PENDING, assigned_at__isnull=False, assigned_at__lte=limit
        )
        for appt in list(stale):
            seen.add(appt.pk)
            release_and_reassign(appt)
            done += 1

        for appt in list(Appointment.objects.filter(status=Appointment.Status.SEARCHING)):
            if appt.pk in seen:
                continue
            assign_provider(appt)
            done += 1

        self.stdout.write(self.style.SUCCESS(f"{done} rendez-vous traités"))
