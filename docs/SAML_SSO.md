# SSO SAML — UCCX Portal

Avec **SSO activé sur CUCM/UCCX**, l’auth mot de passe via AXL `doAuthenticateUser` (ancien SkillsManager PHP) ne fonctionne plus pour les end-users. Le portail agit comme **Service Provider SAML 2.0** vers le **même IdP** (Cisco IdS, Azure AD, ADFS…).

## Flux

1. Bouton **Connexion SSO** → `GET /api/auth/saml/login` → redirect IdP  
2. IdP POST l’assertion vers **ACS** : `{PUBLIC_BASE_URL}/api/auth/saml/acs`  
3. Le portail extrait le `userid`, vérifie que l’utilisateur existe en base (sync UCCX), émet un JWT  

Compte **admin local** (break-glass) : formulaire « Admin local » → `POST /api/auth/login`.

L’API Admin UCCX (`UCCX_USER` / `UCCX_PASSWORD`) reste en Basic Auth application — **indépendante** du SSO end-user.

## Déclaration IdP

| Paramètre SP | Valeur |
|--------------|--------|
| Entity ID | `{PUBLIC_BASE_URL}/api/auth/saml/metadata` (ou override `saml_sp_entity_id`) |
| ACS (HTTP-POST) | `{PUBLIC_BASE_URL}/api/auth/saml/acs` |
| Metadata SP | `GET /api/auth/saml/metadata` |

Configurer côté IdP l’attribut userid (`uid`, `user.userprincipalname`, etc.) et le reporter dans **Settings** → `saml_userid_attr`.

## Settings portail (admin)

Activer et renseigner (chiffré en base) :

- `saml_enabled` = true  
- `saml_idp_metadata_url` **ou** `saml_idp_metadata_xml`  
- `saml_userid_attr`  
- `public_base_url` en HTTPS correct  

Puis **Administration → Users → Sync depuis UCCX**, activer `can_access_apps` / `can_access_skills` et assigner apps / teams.

## Notes sécurité

- Signature d’assertion : le parseur actuel extrait NameID/attributs ; en production IdP, exiger des assertions signées et valider le certificat IdP (évolution `xmlsec` / python3-saml stricte).  
- Ne jamais committer les secrets IdP / UCCX en clair.  
