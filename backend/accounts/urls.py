from django.contrib.auth import views as auth_views
from django.urls import path

from appointments import admin_views
from . import views

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html",                                            redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("redirect/", views.post_login, name="post_login"),
    path("gestion/", admin_views.admin_dashboard, name="admin_dashboard"),
]