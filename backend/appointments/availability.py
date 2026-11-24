from datetime import time

from services.models import Availability

from .models import Appointment

# Un RDV en attente ou confirmé bloque la plage.
# Un RDV annulé (refusé ou annulé) la libère.
BLOCKING = (Appointment.Status.PENDING, Appointment.Status.CONFIRMED)


def slot_bounds(slot_value):
    """'08-10' -> (time(8, 0), time(10, 0))"""
    start, end = (int(h) for h in slot_value.split("-"))
    return time(start), time(end)


def free_slots(provider, date, exclude_pk=None):
    """Valeurs de Appointment.Slot réservables pour ce prestataire à cette date.

    Une plage est libre si :
      1. elle est entièrement incluse dans une Availability du même jour de semaine ;
      2. aucun RDV en attente ou confirmé ne l'occupe déjà.
    """
    windows = list(
        Availability.objects.filter(provider=provider, weekday=date.weekday())
        .values_list("start_time", "end_time")
    )
    taken = Appointment.objects.filter(provider=provider, date=date, status__in=BLOCKING)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    taken_slots = set(taken.values_list("time_slot", flat=True))

    free = []
    for value, _label in Appointment.Slot.choices:
        if value in taken_slots:
            continue
        start, end = slot_bounds(value)
        if any(w_start <= start and end <= w_end for w_start, w_end in windows):
            free.append(value)
    return free


def is_slot_available(provider, date, slot_value, exclude_pk=None):
    return slot_value in free_slots(provider, date, exclude_pk=exclude_pk)