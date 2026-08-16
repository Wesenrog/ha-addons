# Endringslogg

## 1.3.0

- Logger naar entiteten gaar utilgjengelig og tilgjengelig igjen, med
  varighet og siste feilmelding.
- Forkastede samples logges na med de faktiske inngangsverdiene (I1, I3,
  alle tre spenninger, P og Q) - de tre forste i sin helhet, deretter
  hoyst en per minutt slik at loggen ikke drukner.
- Availability publiseres bare ved endring, ikke ved hver melding.
- Statuslinja viser om entiteten er tilgjengelig.

## 1.2.0

- Nytt felt meter_ts: tidsstempelet fra amsreader (feltet "t", konfigurerbart
  via field_ts) sendes videre som ISO 8601.
- Nytt felt last_valid_ts: absolutt tidspunkt for siste gyldige beregning.
- To nye diagnostikk-entiteter med device_class timestamp - Home Assistant
  viser dem som levende relativ tid ("for 2 minutter siden"), og alderen
  fortsetter aa vokse selv om broen slutter aa publisere.
- age_s beholdes, men er et oyeblikksbilde fra siste melding.

## 1.1.1

- Sensoren "Strom L2" barer hele state-meldingen som attributter, slik at
  error, ts og age_s blir tilgjengelige i Home Assistant uten egne
  entiteter - og et enkelt markdown-kort kan vise hele maleoyeblikket.

## 1.1.0

- Publiserer hele maleoyeblikket som EN samlet JSON paa ett state-topic
  (ams/derived/it3/state) i stedet for bare avledet I_L2.
- Elleve entiteter under en felles enhet "Aidon IT-nett (beregnet)":
  strom L1/L2/L3, alle tre linjespenninger, aktiv og reaktiv effekt,
  samt diagnostikk (forkastet losning, fasemargin, gyldig-flagg).
  Alle leser fra samme topic, saa de oppdateres atomisk fra samme sample.
- Malte verdier publiseres alltid ferske; bare den avledede I_L2 holdes
  fra forrige gyldige beregning naar konsistenssjekken slaar ut.
- Den gamle enkeltsensoren it3_il2 fjernes automatisk fra Home Assistant.

## 1.0.6

- Standard src_topic endret til amsreader/power. Med payload-formatet
  "Home-Assistant" sender amsreader maledataene dit, ikke til publish-
  topicet direkte. Endrer ikke eksisterende installasjoner.

## 1.0.5

- Trekker fra eksportert effekt: netto P = P - PO, netto Q = Q - QO.
  Amsreader rapporterer import og eksport hver for seg. Feltene er
  valgfrie, sa meldinger uten dem forkastes ikke.

## 1.0.4

- Logger forste vellykkede beregning og statusoppsummering hvert 5. minutt.
- Advarer hvis ingen meldinger kommer inn, eller hvis meldinger kommer inn
  men ingen kan brukes - og lister da feltnavnene den saa mot dem den leter
  etter, slik at feil field_*-oppsett blir synlig i loggen.

## 1.0.3

- Avslutter med tydelig feilmelding ved avvist brukernavn/passord i stedet
  for aa prove igjen i det uendelige mens tillegget ser friskt ut.

## 1.0.2

- Mulig a sette mqtt_host, mqtt_port, mqtt_user og mqtt_password manuelt.
  Passordfeltet maskeres i UI-et.
- Uten mqtt_host brukes fortsatt MQTT-tjenesten fra Home Assistant.
- services endret fra mqtt:need til mqtt:want, slik at tillegget ogsa
  starter mot en broker utenfor Home Assistant.

## 1.0.1

- Fikset byggefeil: oppgir base-image eksplisitt. Supervisor 2026.04.0
  sender ikke lenger BUILD_FROM automatisk til app-er uten build.yaml.
- Lagt til paakrevde io.hass.*-labels.

## 1.0.0

- Forste versjon. Rekonstruerer I_L2 fra I_L1, I_L3, U12/U23/U31, P og Q.
- Publiserer via MQTT Discovery med valid, lead_deg og alt_a som attributter.
