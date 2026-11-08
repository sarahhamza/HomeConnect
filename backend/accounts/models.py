from django.contrib.auth.models import AbstractUser
from django.db import models


def avatar_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"avatars/user_{instance.pk}.{ext}"


class User(AbstractUser):
    class Role(models.TextChoices):
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Prestataire"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    avatar = models.ImageField("Photo de profil", upload_to=avatar_path, blank=True)
    phone = models.CharField("Téléphone", max_length=20, blank=True)
    city = models.CharField("Ville", max_length=100, blank=True)