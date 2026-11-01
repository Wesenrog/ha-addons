# Zigbee Watch

Fører historikk over Zigbee-nettet fra Zigbee2MQTT - hvem som ruter gjennom
hvem, linkkvalitet, tilgjengelighet og rutefeil - og sier fra når det endrer
seg eller blir dårligere.

Zigbee2MQTT viser nettet slik det er akkurat nå. Zigbee Watch tar vare på det
over tid: inventaret, linkkvalitet fra hver melding, overganger i
tilgjengelighet, feil fra loggen per enhet og et nettverkskart hver natt.
Funnene samles i en liste som vises i Home Assistant, og en rapport viser
historikken per enhet uten SQL.

Bare observatør: det eneste den sender til Zigbee2MQTT er forespørselen om
nettverkskart om natten.

Se [DOCS.md](DOCS.md) for oppsett, entiteter, regler og rapporten.
