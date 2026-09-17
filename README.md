# IT-nett add-ons for Home Assistant

## Installasjon

Innstillinger -> Tillegg -> Tilleggsbutikk -> tre prikker -> Repositories,
og lim inn:

    https://github.com/wesenrog/ha-addons

## IL2 Bridge

Rekonstruerer strommen i L2 paa en Aidon 3P3W-maaler (IT-nett), der maaleren
bare rapporterer I_L1 og I_L3. De to kjente strombelopene, alle tre
linjespenningene, samt P og Q bestemmer fasevinklene entydig - og da gir
Kirchhoffs stromlov I_L2 = -(I_L1 + I_L3).

Publiseres som MQTT Discovery-sensor. Ingen YAML i configuration.yaml.

## Mirror Bridge

Speiler utvalgte entiteter fra en Home Assistant-instans til en annen over MQTT,
til bruk der den ene er eksponert mot internett og bare skal se og styre noen
faa ting.

En entitet speiles ved aa faa etiketten `mirror`, og blir skrivbar med
`mirror-rw`. Ingen YAML, ingen omstart - etiketten er hele inngrepet, og aa
fjerne den trekker tilbake Discovery-meldingen slik at entiteten forsvinner
ryddig paa den andre siden.

Presentasjon leses fra kildeentiteten: enhet, kortnavn, maaleenhet, device
class, ikon, temperaturgrenser og tilgjengelige moduser. Kommandoer valideres
mot entitetens egne attributter - et settpunkt klippes til grensene apparatet
selv oppgir - og medlemskapet er autorisasjonen: en kommando mot noe som ikke
har `mirror-rw` avvises.

Stotter binary_sensor, sensor, switch, climate og light. Ny enhetsklasse legges
til i `classes.py`.
