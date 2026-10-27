from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import authenticate_user, create_access_token, get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.saml_auth import (
    SamlError,
    build_authn_request_redirect,
    extract_userid_from_assertion,
    saml_configured,
    sp_metadata_xml,
)
from app.schemas import MeOut, Token
from app.secure_settings import get_setting_value
from app.user_access import user_to_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Connexion locale (break-glass / comptes à mot de passe).
    Si SSO est activé, le parcours nominal reste /saml/login."""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides"
        )
    return Token(access_token=create_access_token(user.username))


@router.get("/me", response_model=MeOut)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = user_to_out(db, user)
    settings = get_settings()
    saml_on = bool(get_setting_value(db, "saml_enabled", settings.saml_enabled))
    impersonator = getattr(request.state, "impersonator", None)
    return MeOut(
        **data,
        saml_enabled=saml_on and saml_configured(settings),
        impersonating=bool(impersonator),
        impersonator=impersonator,
    )


@router.get("/saml/status")
def saml_status(db: Session = Depends(get_db)):
    settings = get_settings()
    enabled = bool(get_setting_value(db, "saml_enabled", settings.saml_enabled))
    return {
        "enabled": enabled and saml_configured(settings),
        "configured": saml_configured(settings),
    }


@router.get("/saml/login")
def saml_login(db: Session = Depends(get_db)):
    settings = get_settings()
    enabled = bool(get_setting_value(db, "saml_enabled", settings.saml_enabled))
    if not enabled:
        raise HTTPException(503, "SAML SSO désactivé")
    try:
        url = build_authn_request_redirect(settings)
    except SamlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return RedirectResponse(url, status_code=302)


@router.post("/saml/acs")
async def saml_acs(
    request: Request,
    db: Session = Depends(get_db),
    SAMLResponse: str = Form(None),
):
    """Assertion Consumer Service — émet un JWT et redirige vers le portail."""
    settings = get_settings()
    enabled = bool(get_setting_value(db, "saml_enabled", settings.saml_enabled))
    if not enabled:
        raise HTTPException(503, "SAML SSO désactivé")
    form = await request.form()
    saml_b64 = SAMLResponse or form.get("SAMLResponse")
    if not saml_b64:
        raise HTTPException(400, "SAMLResponse manquant")
    try:
        userid = extract_userid_from_assertion(str(saml_b64), settings)
    except SamlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    user = db.query(User).filter(User.username == userid).first()
    if not user:
        raise HTTPException(
            403,
            f"Utilisateur « {userid} » inconnu du portail — synchronisez-le dans Administration → Users",
        )
    if not user.is_active:
        raise HTTPException(403, "Compte désactivé")

    import json

    token = create_access_token(user.username)
    # Page bridge : stocke le JWT puis redirige vers /
    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"/><title>SSO…</title></head>
<body>
<script>
localStorage.setItem("uccx_token", {json.dumps(token)});
location.replace("/");
</script>
<p>Connexion SSO…</p>
</body></html>"""
    return HTMLResponse(html)


@router.get("/saml/metadata")
def saml_metadata():
    xml = sp_metadata_xml(get_settings())
    return Response(content=xml, media_type="application/samlmetadata+xml")
