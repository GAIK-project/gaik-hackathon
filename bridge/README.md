# WhatsApp-silta

Silta ajetaan omistajan koneella. Meta-avaimet pysyvät tässä prosessissa: sovellus Rahtissa vain
tallettaa Metan lähettämät viestit jonoon, ja silta hakee ne sieltä. Puheluita ei käsitellä.

## Käynnistys

```powershell
.\run.ps1 -Status                                                  # vain luku: numero ja tili
.\run.ps1 -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi      # käynnistä (oletus 180 min)
.\run.ps1 -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi -Minutes 0   # kunnes Ctrl+C
```

`run.ps1` lukee `WHATSAPP_ACCESS_TOKEN`- ja `WHATSAPP_APP_SECRET`-avaimet tiedostosta
`~/.agents/env/personal/lajio.env`, sallitut numerot (`LUMI_ALLOWED`) tiedostosta
`lumicode-agents.env` ja `HACK_BRIDGE_KEY`-avaimen repon `.env`-tiedostosta. Jokainen avain
luetaan enintään neljä kertaa; tyhjä tai `encrypted:`-alkuinen arvo hylätään. Arvoja ei tulosteta
eikä tallenneta. Avainten luku kestää noin kymmenen sekuntia per avain.

Graph-versio, puhelinnumeron id ja WhatsApp Business -tilin id ovat tiedostossa `config.json`.

## Mitä tapahtuu

1. Silta avaa sovellukseen istunnon ja saa webhook-osoitteen.
2. Se kokeilee Metan kättelyä itse julkisen osoitteen kautta ja liittää vasta sitten Meta-sovelluksen
   webhookin (kenttä `messages`) osoitteeseen `<julkinen osoite>/api/whatsapp/webhook`.
3. Se hakee viestit pitkällä kyselyllä, tarkistaa allekirjoituksen, ohittaa muut kuin sallitut
   lähettäjät ja jo käsitellyt viestit (`.state/seen.json`).
4. Teksti tai ääniviesti menee sovellukseen raportiksi. Lähettäjä saa yhteenvedon, Word-tiedoston
   ja painikkeet "Approve and send" ja "Correct".
5. "Approve and send" lähettää raportin sovelluksen oletusvastaanottajalle. "Correct" pyytää
   korjauksen, ja seuraava viesti (teksti tai ääni) korjaa raportin.

Silta vastaa vain 24 tunnin sisällä lähettäjän viimeisestä viestistä. Puhelinnumero ei päädy
sovellukseen. Meta-sovelluksella on yksi webhook: sillan käynnistys vie sen muilta saman
Meta-sovelluksen käyttäjiltä.

## Pysäytys

Ctrl+C: silta irrottaa webhookin ja päättää istunnon. Lokissa pitää näkyä `webhook detached`.
Jos ajo katkesi muuten (ikkuna suljettiin, prosessi tapettiin):

```powershell
.\run.ps1 -Stop -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi
```

## Mitä ei ole koskaan ajettu

- Siltaa ei ole ajettu oikeaa palvelinta eikä oikeaa Metaa vasten: webhookin liittämistä ja
  irrotusta, viestin lähetystä, Word-tiedoston latausta ja lähetystä dokumenttina, painikkeita ja
  ääniviestin latausta puhelimesta on kokeiltu vain testeissä valepalvelinta vasten
  (`server/tests/whatsapp/`).
- Ctrl+C:n ja `-Stop`-ajon irrotusta ei ole kokeiltu oikeasti.
- Metan testinumero vastaa vain testisovellukseen lisätyille ja koodilla vahvistetuille numeroille.
- Puheluita ei ole rakennettu: numeroon soitettu puhelu jää vastaamatta.
