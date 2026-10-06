import common
import paho.mqtt.client as mqtt

CONFIG = common.load_config("config.json")
SERVER_HOSTNAME = CONFIG["server_hostname"]
SERVER_MQTT_PORT = CONFIG["server_mqtt_port"]

mqtt_client = mqtt.Client()
mqtt_client.connect(SERVER_HOSTNAME, port=SERVER_MQTT_PORT)
mqtt_client.loop_start()
mqtt_topic = common.render_topic("system_status", "master", "all")
mqtt_client.publish(mqtt_topic, payload="STOP", qos=2)
print("System status set to STOP")
mqtt_client.loop_stop()
mqtt_client.disconnect()
