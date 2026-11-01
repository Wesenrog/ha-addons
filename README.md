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

Stotter binary_sensor, sensor, switch, climate, light (med farge og effekter),
button, input_datetime, input_number, timer, script og scene. Ny enhetsklasse legges til i `classes.py`.

## Zigbee Watch

Forer historikk over Zigbee-nettet fra Zigbee2MQTT - inventar, linkkvalitet,
tilgjengelighet, rutefeil fra loggen og et nettverkskart hver natt - og legger
endringer og forverring i en liste med funn: ny forelder, fallende
linkkvalitet, feil over enhetens eget normalnivaa, og enheter som ikke er hort.

Bare observator: det eneste den sender til Zigbee2MQTT er foresporselen om
nettverkskart om natten. Historikken ligger i SQLite under
`/share/zigbee_watch/`, og `python -m zigbee_watch.report` viser den per enhet
uten SQL.

## Homely

Henter alarmen og sensorene fra et Homely-alarmsystem (Homelys API, bare
lesing) og publiserer dem som MQTT Discovery-entiteter: alarmstatus,
dør/vindu-kontakter, bevegelse, røyk/varme, lås, temperatur, batteri og
tilkobling. Hendelser kommer i sanntid over websocket, med jevnlig henting som
reserve. Data som ikke kan stoles på (brutt forbindelse, enhet frakoblet) vises
som utilgjengelig, ikke som siste verdi. Navn på norsk, faste entitets-id-er.
Se `homely/DOCS.md`.
