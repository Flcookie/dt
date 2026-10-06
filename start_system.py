import common
import paho.mqtt.client as mqtt
import time

CONFIG = common.load_config("config.json")
COMPONENT_WIPS = CONFIG["component_wips"]
SERVER_HOSTNAME = CONFIG["server_hostname"]
SERVER_MQTT_PORT = CONFIG["server_mqtt_port"]
SYSTEM_START_DELAY = CONFIG["system_start_delay"]

mqtt_client = mqtt.Client()
mqtt_client.connect(SERVER_HOSTNAME, port=SERVER_MQTT_PORT)
mqtt_client.loop_start()
for component_id, component_wip in COMPONENT_WIPS.items():
    mqtt_topic = common.render_topic("component_wip", "master", component_id)
    mqtt_payload = common.serialize_object(component_wip)
    mqtt_client.publish(mqtt_topic, payload=mqtt_payload, qos=2)
    print("Component " + component_id + " WIP set to " + mqtt_payload)

time.sleep(SYSTEM_START_DELAY)
mqtt_topic = common.render_topic("system_status", "master", "all")
mqtt_client.publish(mqtt_topic, payload="START", qos=2)
print("System status set to START")
mqtt_client.loop_stop()
mqtt_client.disconnect()
