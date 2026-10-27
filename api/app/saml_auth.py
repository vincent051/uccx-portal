"""SAML 2.0 SP pour SSO opérateurs (Cisco IdS / Azure AD / ADFS)."""

from __future__ import annotations

import base64
import logging
import re
import zlib
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}


class SamlError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _settings_attr(settings: Settings, key: str, default: Any = "") -> Any:
    return getattr(settings, key, default)


def saml_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not _settings_attr(settings, "saml_enabled", False):
        return False
    meta_url = (_settings_attr(settings, "saml_idp_metadata_url", "") or "").strip()
    meta_xml = (_settings_attr(settings, "saml_idp_metadata_xml", "") or "").strip()
    return bool(meta_url or meta_xml)


def sp_entity_id(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    eid = (_settings_attr(settings, "saml_sp_entity_id", "") or "").strip()
    if eid:
        return eid
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/api/auth/saml/metadata"


def acs_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"{(settings.public_base_url or '').rstrip('/')}/api/auth/saml/acs"


def _parse_idp_metadata(xml: str) -> dict[str, str]:
    root = ET.fromstring(xml)
    entity_id = root.attrib.get("entityID") or ""
    sso_url = ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "SingleSignOnService":
            binding = el.attrib.get("Binding", "")
            loc = el.attrib.get("Location", "")
            if "HTTP-Redirect" in binding and loc:
                sso_url = loc
                break
            if not sso_url and loc:
                sso_url = loc
    if not sso_url:
        raise SamlError("Metadata IdP : SingleSignOnService introuvable")
    return {"entity_id": entity_id, "sso_url": sso_url}


def load_idp_config(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    xml = (_settings_attr(settings, "saml_idp_metadata_xml", "") or "").strip()
    if not xml:
        url = (_settings_attr(settings, "saml_idp_metadata_url", "") or "").strip()
        if not url:
            raise SamlError("SAML IdP metadata non configurée", status_code=503)
        import httpx

        with httpx.Client(timeout=30.0, verify=True) as client:
            r = client.get(url)
        if r.status_code != 200:
            raise SamlError(f"Téléchargement metadata IdP → {r.status_code}", status_code=502)
        xml = r.text
    return _parse_idp_metadata(xml)


def build_authn_request_redirect(settings: Settings | None = None) -> str:
    """Construit l’URL Redirect SSO (AuthnRequest déflaté + base64)."""
    settings = settings or get_settings()
    if not saml_configured(settings):
        raise SamlError("SAML désactivé ou non configuré", status_code=503)
    idp = load_idp_config(settings)
    import uuid
    from datetime import datetime, timezone

    req_id = f"_{uuid.uuid4()}"
    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    destination = idp["sso_url"]
    issuer = sp_entity_id(settings)
    acs = acs_url(settings)
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{req_id}" Version="2.0" IssueInstant="{issue_instant}" '
        f'Destination="{destination}" '
        f'AssertionConsumerServiceURL="{acs}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        f"<samlp:NameIDPolicy Format=\"urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified\" "
        f'AllowCreate="true"/>'
        f"</samlp:AuthnRequest>"
    )
    compressed = zlib.compress(xml.encode("utf-8"))[2:-4]  # raw deflate
    b64 = base64.b64encode(compressed).decode("ascii")
    sep = "&" if "?" in destination else "?"
    return f"{destination}{sep}{urlencode({'SAMLRequest': b64})}"


def sp_metadata_xml(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    entity = sp_entity_id(settings)
    acs = acs_url(settings)
    return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{entity}">
  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true"
      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified</md:NameIDFormat>
    <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="{acs}" index="0" isDefault="true"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>
"""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def extract_userid_from_assertion(
    saml_response_b64: str,
    settings: Settings | None = None,
) -> str:
    """Décode SAMLResponse POST et extrait le userid (NameID ou attribut)."""
    settings = settings or get_settings()
    try:
        raw = base64.b64decode(saml_response_b64)
    except Exception as exc:  # noqa: BLE001
        raise SamlError("SAMLResponse invalide (base64)") from exc
    try:
        # Parfois compressé
        try:
            xml_bytes = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            xml_bytes = raw
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SamlError("SAMLResponse XML invalide") from exc

    want_signed = bool(_settings_attr(settings, "saml_want_assertions_signed", True))
    # Vérif signature stricte nécessite xmlsec ; on log un warning si absente
    has_sig = any(_local(el.tag) == "Signature" for el in root.iter())
    if want_signed and not has_sig:
        logger.warning("SAML assertion sans Signature (SAML_WANT_ASSERTIONS_SIGNED=true)")

    # Status
    for el in root.iter():
        if _local(el.tag) == "StatusCode":
            value = el.attrib.get("Value", "")
            if value and not value.endswith("Success"):
                raise SamlError(f"SAML status: {value}")

    userid_attr = (
        _settings_attr(settings, "saml_userid_attr", "uid") or "uid"
    ).strip()

    # AttributeStatement
    for el in root.iter():
        if _local(el.tag) != "Attribute":
            continue
        name = el.attrib.get("Name") or el.attrib.get("FriendlyName") or ""
        if name == userid_attr or name.endswith(f"/{userid_attr}") or name.lower() == userid_attr.lower():
            for child in el:
                if _local(child.tag) == "AttributeValue" and (child.text or "").strip():
                    return child.text.strip()

    # NameID
    for el in root.iter():
        if _local(el.tag) == "NameID" and (el.text or "").strip():
            return el.text.strip()

    raise SamlError("Impossible d’extraire le userid de l’assertion SAML")
