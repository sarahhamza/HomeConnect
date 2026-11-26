"""appointments/admin_views.py : tableau de bord de l'administrateur (réservé au staff).

À brancher dans appointments/matching_urls.py :

    from . import admin_views
    ...
    path("gestion/", admin_views.admin_dashboard, name="admin_dashboard"),
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from services.models import Service

from .matching import OPEN_STATUSES, _service_request_model, ai_health, display_name
from .models import Appointment, Review

User = get_user_model()
S = Appointment.Status

STATUS_COLORS = {                                   # mêmes couleurs que _status_badge.html
    S.CONFIRMED: "#059669", S.PENDING: "#D97706", S.COMPLETED: "#2563EB",
    S.SEARCHING: "#4338CA", S.UNMATCHED: "#6B7280", S.CANCELLED: "#DC2626",
}


def _donut(parts: list, total: int) -> str:
    """conic-gradient du graphique en anneau : [(couleur, nombre), ...]."""
    if not total:
        return "#d8e3fb"
    stops, start = [], 0.0
    for color, n in parts:
        end = start + n * 100 / total
        stops.append(f"{color} {start:.2f}% {end:.2f}%")
        start = end
    return "conic-gradient(" + ", ".join(stops) + ")"


@login_required
@user_passes_test(lambda u: u.is_active and u.is_staff, login_url="login")
def admin_dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    status_label = dict(S.choices)
    category_label = dict(Service.Category.choices)

    # ---------------------------------------------------------------- utilisateurs, services
    users_total = User.objects.count()
    providers = User.objects.filter(role="provider").count()
    new_users = User.objects.filter(date_joined__gte=now - timedelta(days=7)).count()
    services_active = Service.objects.filter(is_active=True).count()
    services_total = Service.objects.count()

    # ---------------------------------------------------------------- rendez-vous
    by_status = {r["status"]: r["n"] for r in
                 Appointment.objects.values("status").annotate(n=Count("pk")).order_by()}
    appts_total = sum(by_status.values())
    status_rows = [{"label": status_label.get(k, k), "n": n, "color": STATUS_COLORS.get(k, "#6B7280"),
                    "pct": round(n * 100 / appts_total) if appts_total else 0}
                   for k, n in sorted(by_status.items(), key=lambda kv: -kv[1])]
    donut = _donut([(r["color"], r["n"]) for r in status_rows], appts_total)
    open_appts = Appointment.objects.filter(status__in=OPEN_STATUSES).count()
    revenue = Appointment.objects.filter(status=S.COMPLETED).aggregate(t=Sum("total_price"))["t"] or 0

    days = [today - timedelta(days=i) for i in range(13, -1, -1)]          # 14 derniers jours
    per_day = {r["d"]: r["n"] for r in
               Appointment.objects.filter(created_at__date__gte=days[0])
               .annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("pk")).order_by()}
    top = max(per_day.values(), default=0)
    series = [{"label": d.strftime("%d/%m"), "n": per_day.get(d, 0),
               "pct": max(6, round(per_day.get(d, 0) * 100 / top)) if top else 6} for d in days]

    cats = list(Appointment.objects.values("service__category").annotate(n=Count("pk"))
                .order_by("-n")[:6])
    cat_top = cats[0]["n"] if cats else 0
    categories = [{"label": category_label.get(c["service__category"], c["service__category"]),
                   "n": c["n"], "pct": round(c["n"] * 100 / cat_top) if cat_top else 0} for c in cats]

    # ---------------------------------------------------------------- besoins clients et offres (app des offres)
    open_needs = offers_pending = 0
    needs_available = False
    ServiceRequest = _service_request_model()
    if ServiceRequest is not None:
        needs_available = True
        open_needs = ServiceRequest.objects.filter(status=ServiceRequest.Status.OPEN).count()
        Offer = ServiceRequest.offers.rel.related_model
        offers_pending = Offer.objects.filter(status=Offer.Status.PENDING).count()

    # ---------------------------------------------------------------- avis, meilleurs prestataires
    reviews = Review.objects.aggregate(avg=Avg("rating"), n=Count("pk"))
    done = list(Appointment.objects.filter(status=S.COMPLETED, provider__isnull=False)
                .values("provider").annotate(n=Count("pk")).order_by("-n")[:5])
    ratings = {r["provider"]: r["avg"] for r in
               Review.objects.values("provider").annotate(avg=Avg("rating")).order_by()}
    names = {u.pk: u for u in User.objects.filter(pk__in=[d["provider"] for d in done])}
    top_providers = [{"user": names.get(d["provider"]), "name": display_name(names[d["provider"]])
                      if d["provider"] in names else "—", "done": d["n"],
                      "rating": ratings.get(d["provider"])} for d in done]

    kpis = [
        {"label": "Utilisateurs", "value": users_total, "icon": "group",
         "sub": f"{providers} prestataire{'s' if providers > 1 else ''} · +{new_users} cette semaine"},
        {"label": "Services publiés", "value": services_active, "icon": "home_repair_service",
         "sub": f"sur {services_total} au total"},
        {"label": "Rendez-vous", "value": appts_total, "icon": "event_available",
         "sub": f"{by_status.get(S.PENDING, 0)} en attente · {by_status.get(S.CONFIRMED, 0)} confirmés"},
        {"label": "Demandes à pourvoir", "value": open_appts + open_needs, "icon": "inbox",
         "sub": f"{open_appts} automatiques · {open_needs} besoins clients"},
        {"label": "Offres en attente", "value": offers_pending, "icon": "send",
         "sub": "en attente de la décision du client"},
        {"label": "Chiffre d'affaires", "value": f"{revenue:.2f} DT", "icon": "payments",
         "sub": "interventions terminées"},
        {"label": "Note moyenne", "value": f"{reviews['avg']:.1f} / 5" if reviews["avg"] else "—",
         "icon": "star", "sub": f"{reviews['n']} avis"},
        {"label": "Terminés", "value": by_status.get(S.COMPLETED, 0), "icon": "task_alt",
         "sub": f"{by_status.get(S.CANCELLED, 0)} annulés"},
    ]

    return render(request, "admin/admin_dashboard.html", {
        "kpis": kpis, "status_rows": status_rows, "donut": donut, "appts_total": appts_total,
        "series": series, "categories": categories, "top_providers": top_providers,
        "needs_available": needs_available,
        "latest_appointments": (Appointment.objects.select_related("client", "provider", "service")
                                .order_by("-created_at")[:8]),
        "latest_users": User.objects.order_by("-date_joined")[:6],
        "health": ai_health(),
    })