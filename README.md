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
