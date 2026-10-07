import secrets
from datetime import timedelta
from functools import wraps
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .crypto import decrypt_secret, encrypt_secret_with_key, unwrap_vault_key, wrap_vault_key
from .forms import EntryForm, MasterPasswordForm, SetupForm
from .models import Entry, VaultConfig

def vault_config():
    return VaultConfig.objects.first()

def remember_vault_key(request, password, config):
    request.session["wrapped_vault_key"] = wrap_vault_key(password, config.key_salt, settings.SECRET_KEY)

def session_vault_key(request):
    wrapped_key = request.session.get("wrapped_vault_key")
    if not wrapped_key:
        return None
    try:
        return unwrap_vault_key(wrapped_key, settings.SECRET_KEY)
    except ValueError:
        return None

def vault_session_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.session.get("vault_authenticated"):
            return redirect("login")
        return view(request, *args, **kwargs)
    return wrapped

def home(request):
    config = vault_config()
    if config is None:
        return redirect("setup")
    if not request.session.get("vault_authenticated"):
        return redirect("login")
    entries = Entry.objects.order_by("title")
    return render(request, "vault/home.html", {"entries": entries, "now": timezone.now()})

def setup(request):
    if vault_config() is not None:
        return redirect("login")
    form = SetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        config = VaultConfig.objects.create(password_hash=make_password(password), key_salt=secrets.token_hex(16))
        request.session.cycle_key()
        request.session["vault_authenticated"] = True
        remember_vault_key(request, password, config)
        return redirect("home")
    return render(request, "vault/setup.html", {"form": form})

def login_view(request):
    if vault_config() is None:
        return redirect("setup")
    form = MasterPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        config = vault_config()
        if check_password(form.cleaned_data["password"], config.password_hash):
            request.session.cycle_key()
            request.session["vault_authenticated"] = True
            remember_vault_key(request, form.cleaned_data["password"], config)
            return redirect("home")
        form.add_error("password", "Incorrect master password.")
    return render(request, "vault/login.html", {"form": form})

def logout_view(request):
    if request.method != "POST":
        return HttpResponseForbidden()
    request.session.flush()
    return redirect("login")

def authenticated_entry(request, entry_id):
    return get_object_or_404(Entry, pk=entry_id)

def create_entry(request):
    if not request.session.get("vault_authenticated"):
        return redirect("login")
    vault_key = session_vault_key(request)
    if vault_key is None:
        request.session.flush()
        return redirect("login")
    form = EntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["secret"]
        entry = form.save(commit=False)
        entry.delay_seconds = form.cleaned_data["delay_seconds"]
        entry.unlock_duration_seconds = form.cleaned_data["unlock_duration_seconds"]
        entry.secret_ciphertext = encrypt_secret_with_key(vault_key, password)
        entry.save()
        return redirect("home")
    return render(request, "vault/entry_form.html", {"form": form, "heading": "Add an entry", "editing": False})

@vault_session_required
def entry_detail(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    return render(request, "vault/detail.html", {"entry": entry, "now": timezone.now()})

@vault_session_required
def edit_entry(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    vault_key = session_vault_key(request)
    if vault_key is None:
        request.session.flush()
        return redirect("login")
    form = EntryForm(request.POST or None, instance=entry, current_delay=entry.delay_seconds, current_unlock_duration=entry.unlock_duration_seconds)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.delay_seconds = form.cleaned_data["delay_seconds"]
        entry.unlock_duration_seconds = form.cleaned_data["unlock_duration_seconds"]
        secret = form.cleaned_data["secret"]
        if secret:
            entry.secret_ciphertext = encrypt_secret_with_key(vault_key, secret)
        entry.save()
        return redirect("entry_detail", entry_id=entry.pk)
    return render(request, "vault/entry_form.html", {"form": form, "heading": "Edit entry", "editing": True, "entry": entry})

@vault_session_required
def delete_entry(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    if request.method == "POST":
        entry.delete()
        return redirect("home")
    return render(request, "vault/delete.html", {"entry": entry})

@vault_session_required
def request_unlock(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    if request.method == "POST":
        if not entry.unlock_pending and not entry.unlocked:
            entry.unlock_requested_at = timezone.now()
            entry.unlock_at = entry.unlock_requested_at + timedelta(seconds=entry.delay_seconds)
            entry.unlock_expires_at = entry.unlock_at + timedelta(seconds=entry.unlock_duration_seconds)
            entry.save(update_fields=["unlock_requested_at", "unlock_at", "unlock_expires_at", "updated_at"])
        return redirect("entry_detail", entry_id=entry.pk)
    return HttpResponseForbidden()

@vault_session_required
def cancel_unlock(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    if request.method == "POST":
        entry.unlock_requested_at = None
        entry.unlock_at = None
        entry.unlock_expires_at = None
        entry.save(update_fields=["unlock_requested_at", "unlock_at", "unlock_expires_at", "updated_at"])
        return redirect("entry_detail", entry_id=entry.pk)
    return HttpResponseForbidden()

@vault_session_required
def reveal_entry(request, entry_id):
    entry = authenticated_entry(request, entry_id)
    if request.method != "POST":
        return HttpResponseForbidden()
    if not entry.unlocked:
        return HttpResponseForbidden("This entry is locked or its unlock period has ended.")
    form = MasterPasswordForm(request.POST)
    secret = None
    if form.is_valid():
        config = vault_config()
        if check_password(form.cleaned_data["password"], config.password_hash):
            try:
                secret = decrypt_secret(form.cleaned_data["password"], config.key_salt, entry.secret_ciphertext)
            except ValueError as error:
                form.add_error("password", str(error))
            if secret is not None and not entry.unlocked:
                return HttpResponseForbidden("The unlock period has ended.")
        else:
            form.add_error("password", "Incorrect master password.")
    secret_characters = []
    if secret is not None:
        for character in secret:
            kind = "letter" if character.isalpha() else "number" if character.isdigit() else "symbol"
            secret_characters.append((character, kind))
    return render(request, "vault/reveal.html", {"entry": entry, "form": form, "secret": secret, "secret_characters": secret_characters})
