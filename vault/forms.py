from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from .models import Entry

class MasterPasswordForm(forms.Form):
    password = forms.CharField(label="Master password", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

class SetupForm(forms.Form):
    password = forms.CharField(min_length=14, label="Master password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), help_text="Use at least 14 characters. A long, unique passphrase is recommended.")
    confirm = forms.CharField(label="Confirm master password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm"):
            self.add_error("confirm", "The passwords do not match.")
        return cleaned

class EntryForm(forms.ModelForm):
    secret = forms.CharField(required=False, label="Password or secret", widget=forms.PasswordInput(), help_text="Stored encrypted. Leave unchanged while editing to keep the current secret.")
    website = forms.CharField(required=False, max_length=500, label="Website", widget=forms.TextInput(attrs={"placeholder": "example.com or https://sub.example.com/path"}), help_text="Enter a domain, subdomain, or full URL.")
    delay_amount = forms.IntegerField(min_value=1, max_value=365, initial=1, label="Delay amount")
    delay_unit = forms.ChoiceField(choices=[("seconds", "seconds"), ("minutes", "minutes"), ("hours", "hours"), ("days", "days")], initial="hours", label="Delay unit")
    unlock_duration_amount = forms.IntegerField(min_value=1, max_value=365, initial=15, label="Unlock duration amount")
    unlock_duration_unit = forms.ChoiceField(choices=[("seconds", "seconds"), ("minutes", "minutes"), ("hours", "hours"), ("days", "days")], initial="minutes", label="Unlock duration unit")

    class Meta:
        model = Entry
        fields = ["title", "username", "website", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, current_delay=None, current_unlock_duration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret"].required = not bool(self.instance and self.instance.pk)
        if current_delay is not None:
            self._set_duration_initial("delay", current_delay)
        if current_unlock_duration is not None:
            self._set_duration_initial("unlock_duration", current_unlock_duration)

    def _set_duration_initial(self, prefix, seconds):
        for amount, unit, unit_seconds in [(seconds // 86400, "days", 86400), (seconds // 3600, "hours", 3600), (seconds // 60, "minutes", 60), (seconds, "seconds", 1)]:
            if amount > 0 and seconds % unit_seconds == 0:
                self.fields[f"{prefix}_amount"].initial = amount
                self.fields[f"{prefix}_unit"].initial = unit
                return

    def clean(self):
        cleaned = super().clean()
        website = (cleaned.get("website") or "").strip()
        if website:
            candidate = website if website.lower().startswith(("http://", "https://")) else f"https://{website}"
            try:
                URLValidator(schemes=["http", "https"])(candidate)
            except ValidationError:
                self.add_error("website", "Enter a domain or an HTTP(S) URL, such as example.com or https://example.com/path.")
            else:
                cleaned["website"] = website

        multipliers = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
        for prefix, model_field in [("delay", "delay_seconds"), ("unlock_duration", "unlock_duration_seconds")]:
            amount = cleaned.get(f"{prefix}_amount")
            unit = cleaned.get(f"{prefix}_unit")
            if amount is not None and unit:
                cleaned[model_field] = amount * multipliers[unit]
        return cleaned
