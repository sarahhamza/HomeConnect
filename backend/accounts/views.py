from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import ProfileForm, RegisterForm, StyledPasswordChangeForm


def register(request):
    if request.user.is_authenticated:
        return redirect("post_login")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("post_login")
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    profile_form = ProfileForm(instance=request.user)
    password_form = StyledPasswordChangeForm(request.user)

    if request.method == "POST":
        if "save_profile" in request.POST:
            profile_form = ProfileForm(request.POST, request.FILES, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profil mis à jour.")
                return redirect("profile")
            # formulaire invalide : annuler les valeurs non enregistrées posées sur request.user
            request.user.refresh_from_db()
        elif "remove_avatar" in request.POST:
            if request.user.avatar:
                request.user.avatar.delete(save=True)
            messages.success(request, "Photo supprimée.")
            return redirect("profile")
        elif "change_password" in request.POST:
            password_form = StyledPasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # garde la session active
                messages.success(request, "Mot de passe modifié avec succès.")
                return redirect("profile")

    return render(request, "accounts/profile.html", {
        "form": profile_form,
        "password_form": password_form,
        "base_template": ("base_provider.html" if request.user.role == "provider"
                          else "base_client.html"),
    })


@login_required
def post_login(request):
    if request.user.role == "client":
        return redirect("client_dashboard")
    return redirect("provider_dashboard")