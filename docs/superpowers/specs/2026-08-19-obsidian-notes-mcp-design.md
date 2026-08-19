# Obsidian-vault raadplegen en beschrijven vanaf de telefoon

**Datum:** 2026-08-19 · **Status:** ontwerp, wacht op goedkeuring

## Doel

Vanaf de telefoon in de Obsidian-vaults kunnen zoeken, lezen en schrijven via Claude, zonder dat Obsidian zelf verandert en zonder dat de Mac aan hoeft te staan.

Concreet: onderweg kunnen vragen *"wat schreef ik over de wheel op DECK?"* en een research-log kunnen laten aanmaken die volgens de eigen frontmatter-regels is opgemaakt en via sync op de Mac terechtkomt.

## Randvoorwaarden

| | |
|---|---|
| Mac staat niet altijd aan | De oplossing mag er niet van afhangen |
| Obsidian blijft Obsidian | Zelfde `.md`-bestanden, wikilinks, frontmatter, plugins |
| Gratis | Binnen de gratis lagen van Supabase en Cloud Run |
| Twee apparaten | Mac en iPhone, beide met een werkende vault |

## Huidige situatie

Vijf vaults in `~/Documents/Obsidian`, geen daarvan een git-repo:

| vault | `.md`-bestanden | markdown |
|---|---|---|
| portfolio-vault | 177 | 1,15 MB |
| lazytheta-vault | 33 | 0,31 MB |
| puppy-klantreis-vault | 18 | 0,10 MB |
| tastytrade-mcp-vault | 9 | 0,05 MB |
| prins-social-tracker-vault | 7 | 0,02 MB |
| **totaal** | **244** | **1,63 MB** |

De totale mapgrootte is 13 MB; het verschil zit in afbeeldingen en bijlagen.

Bestaand en herbruikbaar: `lazytheta-mcp-cloudrun` (939 regels, 34 tools) met een OAuth 2.1 + PKCE-brug naar Supabase Auth in `mcp_auth.py` (489 regels). Eén Supabase-project (`dacmqkjvofqqjfsfrtlp`, eu-west-3), organisatie op het free plan.

## Architectuur

```
iPhone Obsidian ─┐
                 ├─ Remotely Save ──→ Supabase Storage (bucket "vaults")
Mac Obsidian ────┘                             ↑
                                               │ S3-protocol
Claude (telefoon/web) ──→ notes-mcp (Cloud Run) ┘
                              │
                              └─ gedeelde mcp_auth → Supabase Auth JWT → user_id
```

Vier onderdelen met elk één taak:

- **De bucket** is de enige waarheid. Er bestaat geen tweede kopie van een notitie.
- **Remotely Save** is het enige dat Obsidian aanraakt.
- **De MCP** praat alleen met de bucket, nooit met Obsidian.
- **De auth-module** stelt vast wie je bent, en verder niets.

### Waarom een aparte service

De notities-MCP krijgt schrijfrechten op de complete persoonlijke kennisbank, over vijf inhoudelijk losstaande projecten. Dat is een veel bredere bevoegdheid dan de LazyTheta-server heeft. Gescheiden draaien begrenst wat een fout of een lek aan beide kanten kan bereiken, en een slechte deploy legt niet de portefeuilletools plat.

Dit is een andere afweging dan bij Trading 212 (zie vault-notitie 27), waar aanbouwen juist klopte: dat was dezelfde data voor dezelfde app.

### Waarom wel dezelfde repo

De blast radius is het draaiende proces, niet de repo. `mcp_auth.py` verhuist naar de repo-root als gedeelde module, zodat beide services hem importeren. **Kopiëren is expliciet verboden:** dubbele implementaties van hetzelfde hebben in dit project al drie keer een bug opgeleverd (de FIFO-berekening, het WACC-paneel, de fundamentals in twee eenheden).

### Build en deploy — de valkuil

Er is nu **één** Dockerfile, in de repo-root, en die is specifiek voor LazyTheta: hij kopieert `lazytheta-mcp-cloudrun/main.py` en start die. De deploy draait `gcloud run deploy --source .` vanaf de root en pakt precies die.

Een tweede service kan hem dus niet hergebruiken — hij zou de verkeerde handler starten. En een eigen Dockerfile in `notes-mcp-cloudrun/` komt niet bij `mcp_auth.py` in de root, want dan is de build-context die submap.

Daarom:

- `Dockerfile` blijft ongewijzigd van LazyTheta.
- `Dockerfile.notes` komt erbij in de root, met de root als build-context, en kopieert alleen `notes-mcp-cloudrun/*` plus de gedeelde `mcp_auth.py`.
- De notities-service deployt via een expliciete build (`gcloud builds submit` met een `cloudbuild.yaml` die de juiste Dockerfile aanwijst, daarna `gcloud run deploy --image`), omdat `--source .` geen alternatieve Dockerfile kiest.

Dit is de stap die over het hoofd gezien de meeste tijd kost. De deploy-instructies gaan in de README van de nieuwe service, naar het voorbeeld van de bestaande.

### Waarom de opslag níét gescheiden wordt

De vault komt in het bestaande Supabase-project, niet in een tweede. Het gratis plan pauzeert projecten na zeven dagen zonder activiteit, en een project dat alleen notities host wordt precies zo weinig aangeraakt dat dat een reëel risico is. Het bestaande project wordt dagelijks door de Streamlit-app gebruikt en pauzeert dus niet.

**Aanvaarde koppeling:** stopt LazyTheta, dan valt de vault-sync stil. Op te lossen door het project actief te houden of te verhuizen, maar het moet bekend zijn.

## Fase 1 — synchroniseren en lezen

Op zichzelf bruikbaar. Er wordt niets geschreven, dus er kan per definitie niets aan de notities stukgaan.

### Opzet

1. Bucket `vaults` in het bestaande Supabase-project; S3-protocol aan, sleutelpaar gegenereerd.
2. Kopie van `~/Documents/Obsidian` gemaakt vóór de eerste sync.
3. Remotely Save op de Mac, per vault geconfigureerd, **end-to-end-encryptie uit** — anders kan de server de bestanden niet lezen. De beveiliging zit in de buckettoegang.
4. Vaults op de iPhone ingericht (community-plugins installeren op iOS vereist dat de vault eerst op de desktop is klaargezet en overgezet).
5. Sync bij opstarten en op een interval van vijf minuten, om het venster waarin twee versies naast elkaar bestaan klein te houden.

### Tools

| tool | argumenten | geeft terug |
|---|---|---|
| `list_vaults` | — | vaultnamen, aantal notities, pad van `CLAUDE.md` indien aanwezig |
| `search_notes` | `query`, `vault` (optioneel) | pad, titel en het matchende tekstfragment per treffer |
| `read_note` | `path` | inhoud + `revision` |

`list_vaults` meldt of er een `CLAUDE.md` in de vault ligt, zodat de eigen schrijfregels — de verplichte frontmatter in `portfolio-vault`, de mapindeling — gevonden worden vóórdat er in fase 2 geschreven wordt.

`read_note` geeft nu al een `revision` terug, ook al doet fase 1 er niets mee. Zo hoeft het contract in fase 2 niet te veranderen.

### Zoeken zonder index

`search_notes` haalt de `.md`-objecten op (desgevraagd beperkt tot één vault) en zoekt daarin. Geen zoekindex, geen tweede kopie die uit de pas kan lopen.

Dat kan omdat de schaal het toelaat: 1,63 MB over 244 bestanden. Bij 5 GB gratis egress per maand zijn dat ongeveer drieduizend volledige zoekopdrachten.

**Herzien wanneer** de gezamenlijke markdown boven ~10 MB komt of het aantal bestanden boven ~1.000. Dan wordt per zoekopdracht alles ophalen te traag en te duur, en is een afgeleide index in Postgres de volgende stap — met de eigenschap dat de index alleen bepaalt *welke* notities kandidaat zijn en `read_note` altijd de bucket leest, zodat een verouderde index een treffer kan missen maar nooit verkeerde inhoud kan tonen.

### Klaar wanneer

- Vanaf de iPhone een vraag over de inhoud van `portfolio-vault` beantwoord krijgen.
- Een notitie die op de Mac is aangepast is binnen vijf minuten via de tools zichtbaar.
- Obsidian werkt op beide apparaten ongewijzigd: wikilinks, frontmatter, plugins, grafiekweergave.

## Fase 2 — schrijven

Pas beginnen als fase 1 staat en bevalt.

### Tools

| tool | argumenten | gedrag |
|---|---|---|
| `list_notes` | `vault`, `folder` (optioneel) | bestandsstructuur, los van inhoud |
| `write_note` | `path`, `content`, `base_revision` (bij bestaande) | maakt aan of vervangt |
| `append_to_note` | `path`, `text` | plakt achteraan |

Geen `delete_note`, geen hernoemen. De winst is nul en de schade onbegrensd; weggooien gebeurt in Obsidian, waar zichtbaar is wat er weggaat.

### Gelijktijdig schrijven

**Gegarandeerd:** Claude overschrijft nooit iets wat hij niet gezien heeft.

`read_note` geeft een `revision` (de ETag van het object). `write_note` op een bestaande notitie moet die meesturen. De server haalt de actuele ETag op en vergelijkt; wijkt hij af, dan wordt geweigerd en komt de huidige inhoud terug zodat er samengevoegd kan worden. Dezelfde discipline als `save_to_watchlist` sinds die is gaan mergen.

Een nieuwe notitie mag zonder `base_revision`, maar faalt als het pad al bestaat — anders is aanmaken een verkapte overschrijving.

`append_to_note` is onder water ook lezen-wijzigen-schrijven, want S3 kent geen atomaire append. Bij een botsing probeert de server het eenmaal opnieuw met de verse inhoud. Bij toevoegen aan het eind is dat veilig; bij vervangen zou het dat niet zijn.

**Niet gegarandeerd:** dat de Mac geen ongesynchroniseerde wijzigingen heeft. Bewerk je om 10:00 lokaal een notitie en schrijft Claude om 10:01 in de bucket, dan ziet Remotely Save om 10:05 twee versies, en wat er dan gebeurt bepaalt die plugin. Het interval van vijf minuten verkleint dat venster; wegnemen kan niet.

### Klaar wanneer

- Een nieuwe research-log-notitie vanaf de telefoon aanmaken, met geldige frontmatter volgens `portfolio-vault/CLAUDE.md`, en hem daarna op de Mac in Obsidian zien staan.
- Een schrijfpoging op een notitie die intussen is gewijzigd, wordt geweigerd met de actuele inhoud erbij.

## Beveiliging

- **`user_id` komt uit het JWT, nooit uit een argument.** Objecten liggen onder `{user_id}/{vault}/...`; de prefix wordt serverkant voorgezet en is voor de aanroeper onbereikbaar. Dit is de les uit het `load_credential`-lek: zodra een functie ook in een service-role-context draait, is "RLS regelt het" een aanname en geen bescherming.
- **Padvalidatie** serverkant: geen `..`, niets absoluuts, moet op `.md` eindigen.
- **S3-sleutels** in Secret Manager, nooit in code of image.
- Remotely Save's end-to-end-encryptie staat uit; de vertrouwelijkheid leunt op de buckettoegang en de S3-sleutels.

## Foutafhandeling

**Een transportfout mag nooit op data lijken.** Is de bucket onbereikbaar, dan komt er een fout terug — geen lege lijst. "Geen notities gevonden" terwijl Supabase plat ligt is een onware uitspraak over de vault, en precies de fout die `EdgarFetchError` in dit project moest afvangen.

Concreet:

- Bucket onbereikbaar of sleutel ongeldig → expliciete fout met de oorzaak.
- Notitie bestaat niet → onderscheiden van "leeg bestand".
- ETag-botsing bij schrijven → eigen foutsoort met de actuele inhoud erbij, geen generieke 500.

## Tests

De opslaglaag wordt geïnjecteerd, zoals `compute_screener(universe, fetch)` dat doet, zodat alles zonder Supabase draait.

- Padvalidatie: `..`, absolute paden, niet-`.md`, en een pad dat de prefix van een andere gebruiker probeert te raken.
- `user_id` uit het JWT wint van een `user_id` in de argumenten.
- Revisievergelijking: gelijk → schrijven, afwijkend → weigeren mét actuele inhoud.
- Aanmaken op een bestaand pad faalt.
- `append_to_note` na een botsing levert beide stukken tekst op.
- Bucketfout wordt een fout, geen lege uitkomst.

## Buiten scope

- Verwijderen en hernoemen van notities.
- Versiegeschiedenis. Later toe te voegen door de bucket periodiek naar een repo te spiegelen, zonder dat Obsidian daarvan hoeft te weten.
- Afbeeldingen en bijlagen — de tools werken op markdown; de sync neemt de rest gewoon mee.
- Frontmatter valideren in de server. De regels staan in de `CLAUDE.md` van elke vault en horen daar; de server levert ze aan en dwingt ze niet af.

## Bekende risico's

| risico | omgang |
|---|---|
| Remotely Save is een community-plugin, geen officiële sync | Kopie vóór de eerste sync; het is 13 MB |
| Eerste migratie van vijf vaults | Één vault tegelijk, met portfolio-vault als laatste |
| Botsing tussen ongesynchroniseerde Mac-wijziging en een schrijfactie | Kort syncinterval; niet volledig weg te nemen |
| Supabase free plan pauzeert bij zeven dagen inactiviteit | Bestaand, dagelijks gebruikt project |
| Community-plugin installeren op iOS | Vault eerst op de desktop inrichten en overzetten |
| Tweede Cloud Run-service naast de bestaande Dockerfile | Aparte `Dockerfile.notes` met de repo-root als context; zie "Build en deploy" |

## Wat dit kost

Niets, bij het huidige gebruik. Supabase free plan geeft 1 GB opslag (gebruik: 13 MB) en 5 GB egress per maand. Cloud Run draait de bestaande MCP op ~2.500 requests per maand volledig binnen de gratis laag; een tweede service met dit volume ook.

De enige lopende rekening blijft Artifact Registry, ~$0,23 per maand voor de opgeslagen images. Een tweede service voegt daar images aan toe; oude revisies opruimen houdt het onder de gratis grens van 0,5 GB.
