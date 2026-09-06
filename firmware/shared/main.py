import time
from address import ensure_address

ensure_address()

import benq_halo
import benq_halo.halo_mqtt as halo_mqtt

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

benq_halo2 = benq_halo.benq_halo()
lamp_status = benq_halo2.request_lamp_status(command=0x00)

onoff_status = halo_mqtt.HaMqttSwitch(name="onoff_status", full_name="On/Off", switch=benq_halo.OnOff(benq_halo2), 
                                             pow_status=lamp_status['onoff_status'], icon="mdi:power")

front_light = halo_mqtt.HaMqttBrightnessLightWithColorTemp(name="front_light", full_name="Front Light",
                                             light=benq_halo.FrontLamp(benq_halo2), 
                                             pow_status=lamp_status['front_lamp_status'], 
                                             dim_status=lamp_status['front_lamp_brightness'], 
                                             color_temp=lamp_status['front_lamp_color_temp'],
                                             availability=lamp_status['onoff_status'])

back_light = halo_mqtt.HaMqttBrightnessLight(name="back_light", full_name="Back Light", light=benq_halo.BackLamp(benq_halo2), 
                                             pow_status=lamp_status['back_lamp_status'], 
                                             dim_status=lamp_status['back_lamp_brightness'],
                                             availability=lamp_status['onoff_status'])

ultrasonic_sensor = halo_mqtt.HaMqttSwitch(name="ultrasonic_sensor", full_name="Ultrasonic Sensor", switch=benq_halo.UltrasonicSensor(benq_halo2), 
                                             pow_status=lamp_status['ultrasonic_sensor_status'], icon="mdi:motion-sensor")

halo_mqtt.HaMqttButton(name="auto_config", full_name="Auto", button=benq_halo.AutoConfig(benq_halo2), icon="mdi:auto-mode")

def update_mqtt_entities():
    ultrasonic_sensor.update(benq_halo2.ultrasonic_sensor_status)
    onoff_status.update(benq_halo2.onoff_status)
    back_light.update(benq_halo2.back_lamp_status, benq_halo2.back_lamp_brightness, benq_halo2.onoff_status)
    front_light.update(benq_halo2.front_lamp_status, benq_halo2.front_lamp_brightness, benq_halo2.front_lamp_color_temp, benq_halo2.onoff_status)

async def main():
    interval_ms = 5000
    last_run = time.ticks_ms()
    while True:
        await asyncio.sleep_ms(100)

        # Sniff messages from remote control.
        if benq_halo2.receive_without_ack():
            update_mqtt_entities()
            last_run = time.ticks_ms()

        if time.ticks_diff(time.ticks_ms(), last_run) >= interval_ms:
            benq_halo2.request_lamp_status()
            update_mqtt_entities()
            last_run = time.ticks_ms()


try:
    asyncio.run(main())
finally:
    halo_mqtt.close_client()
