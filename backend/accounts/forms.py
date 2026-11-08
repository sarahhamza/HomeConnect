from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm

from .models import User

INPUT = ("w-full h-11 px-4 rounded-xl bg-surface-container-low border border-outline-variant "
         "text-on-surface placeholder:text-outline focus:outline-none focus:ring-2 "
         "focus:ring-primary/30 focus:border-primary transition")


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(
        choices=User.Role.choices,
        label="Je suis",
        widget=forms.RadioSelect,
        initial=User.Role.CLIENT,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role", "password1", "password2")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("avatar", "first_name", "last_name", "email", "phone", "city")
        labels = {"first_name": "Prénom", "last_name": "Nom", "email": "Adresse email"}
        widgets = {
            "avatar": forms.FileInput(attrs={"accept": "image/*", "class": "hidden",
                                             "id": "id_avatar"}),
            "first_name": forms.TextInput(attrs={"class": INPUT, "placeholder": "Prénom"}),
            "last_name": forms.TextInput(attrs={"class": INPUT, "placeholder": "Nom"}),
            "email": forms.EmailInput(attrs={"class": INPUT}),
            "phone": forms.TextInput(attrs={"class": INPUT, "placeholder": "+33 6 12 34 56 78"}),
            "city": forms.TextInput(attrs={"class": INPUT, "placeholder": "Paris"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        # n'exiger l'unicité que si l'email a réellement changé
        if email == (self.instance.email or "").strip().lower():
            return email
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette adresse email est déjà utilisée.")
        return email

    def clean_avatar(self):
        img = self.cleaned_data.get("avatar")
        if img and hasattr(img, "size") and img.size > 2 * 1024 * 1024:
            raise forms.ValidationError("L'image ne doit pas dépasser 2 Mo.")
        return img


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {"old_password": "Mot de passe actuel",
                  "new_password1": "Nouveau mot de passe",
                  "new_password2": "Confirmer le nouveau mot de passe"}
        for name, field in self.fields.items():
            field.label = labels[name]
            field.widget.attrs.update({"class": INPUT})