# Broker = NATIVE mosquitto op sv01 zelf (1883, bind 192.168.1.10).
# Geen container-broker: Ubuntu AppArmor confineert mosquitto; docker-variant is hier bewust onmogelijk (zie 00-sv01.conf).
# Creds user "hasp": /opt/hasp-secrets/hasp.env  (ACL: /etc/mosquitto/acl.file)
# Deze stack bevat alleen de Homey-bridge:
#   homey/state/<id>[/<cap>]  (retained, alle 10s poll)
#   homey/overview
# commando: MQTT publish op homey/cmd/<devid>/<cap>/set   (PUT capability)
# flow:      MQTT publish op homey/cmd/flow[/<id>] met de flow-id(s) IN DE PAYLOAD
#            (payload.split(",") - payload is leidend, niet het topic)
