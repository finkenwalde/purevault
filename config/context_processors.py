def vault_session(request):
    if request.session.get("vault_authenticated"):
        return {"vault_session_expires_at": int(request.session.get_expiry_date().timestamp() * 1000)}
    return {}
