# CheckBH — chargement XML + failover

## Format horaires (`Str_WeekHours`)

Par jour (`Lu` … `Di`) :

- Ouvert : `07:30-18:30` (HH:MM-HH:MM)
- Fermé : `Closed`

Dans le XML public :

```xml
<weekHours>
  <Lu>07:30-18:30</Lu>
  <Ma>07:30-18:30</Ma>
  <Me>07:30-18:30</Me>
  <Je>07:30-18:30</Je>
  <Ve>07:30-18:30</Ve>
  <Sa>Closed</Sa>
  <Di>Closed</Di>
</weekHours>
<Str_WeekHours>Lu=07:30-18:30;Ma=07:30-18:30;Me=07:30-18:30;Je=07:30-18:30;Ve=07:30-18:30;Sa=Closed;Di=Closed</Str_WeekHours>
```

## Format fériés (`Str_Holidays`)

Entrées séparées par `;` :

- `25/12/XXXX 00:00-23:59` (récurrent chaque année)
- `01/01/2027 00:00-23:59` (date fixe)

Élément XML : `/config/Str_Holidays`

## URL Docker (primary)

HTTPS via Traefik (sans basic auth sur `/api/public`) :

```
https://collab-edge.i-hub.com/api/public/apps/<ApplicationName>/config.xml
```

Dev local sans Traefik :

```
http://<IP-WSL-ou-Windows>:8080/api/public/apps/<ApplicationName>/config.xml
```


Dans Script Editor :

1. **Create URL Document** → URL ci-dessus, timeout court (2–3 s)
2. Si succès → **Get XML Document Data** (XPath) :
   - `//weekHours/Lu` … `//weekHours/Di`
   - `//Str_Holidays`
   - `//flags/forceClosed`, `//flags/forceOpen`
   - `//prompts/closed`, `//prompts/welcome`
3. Affecter les variables jour / `Str_Holidays` puis appeler **CheckBH** (ou logique équivalente)

## Failover Document Management (Docker HS)

À chaque sauvegarde, le portail pousse le même fichier vers :

`DOC[en\<ApplicationName>_config.xml]`

ex. `DOC[en\DemoApp_config.xml]` (fichier plat sous la langue `en` — sync portail)

Dans le script, sur échec URL :

1. **Create File Document** / document repository local
2. Mêmes XPath que ci-dessus
3. Continuer CheckBH

Ainsi un appel fonctionne même si le conteneur Docker est arrêté (dernière config synchronisée).

## Booléens

- `forceClosed=true` → traiter comme fermé (dans le script parent, avant/après CheckBH)
- `forceOpen=true` → forcer ouvert (priorité à définir dans le script parent)

## Prompts

Chemins `SP[App\fichier.wav]` dans `//prompts/closed` et `//prompts/welcome`.
Upload via le portail → repository prompts UCCX.
