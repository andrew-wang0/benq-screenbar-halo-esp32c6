# BenQ ScreenBar HALO 2 - integration with Home Assistant

This a PoC for integrating BenQ ScreenBar HALO 2 with Home Assistant.
The project is written on MicroPython and uses [MicroPython Asynchronous MQTT](https://github.com/peterhinch/micropython-mqtt) library by Peter Hinch, 
as well as ideas from [hatank](https://github.com/rguillon/hatank) by Renaud Guillon.

### The required hardware:
- Transceiver [BM5602-60-1](https://www.holtek.com/page/vg/BM5602-60-1)
- Raspberry Pi Pico W

### Connection diagram

![](img/connection_diagramm.png)

| BM5602-60-1 | Raspberry Pi Pico W |
|-------------|---------------------|
| CSN         | GP1 (SPI0 CSn)      |
| SCK         | GP2 (SPI0 SCK)      |
| SDIO        | GP3 (SPI0 TX)       |
| GIO2        | GP4 (SPI0 RX)       |
| VSS         | GND                 |
| VDD         | +3.3V               |

WiFi and MQTT credentials can be configured in [halo_mqtt.py](src/benq_halo/halo_mqtt.py)
The address for communication HALO2_ADDRESS can be configured in [benq_halo](src/benq_halo/__init__.py).
NB! it has reverse byte order compared to the captured data in RF packet, ref to [BC5602 datasheet](https://www.holtek.com/webapi/116711/BC5602v120.pdf), **Bit ordering** section.

The address during the pairing process is **E2 08 00 B0**, but the communication address is most probably transferred during pairing process in an encoded/encrypted form.
To find the communication address, I used [HackRF One](https://en.wikipedia.org/wiki/HackRF_One) and [Universal Radio Hacker (URH)](https://github.com/jopohl/urh)

In FCCID report for the Halo 2 mentioned three radio channels, but I observed communication only on channel 1.
The modulation is GFSK and the data rate is 125 Kbps.

* Channel 1 -> 2405 MHz (default)
* Channel 2 -> 2446
* Channel 3 -> 2475

### Starting to capture the RF signal in URH
![Starting capturing the RF signal in URH](img/urh_screenshot1.png)

### Decoding the captured RF signal in URH
![Decoding the captured RF signal in URH](img/urh_screenshot2.png)

### RF packet format
![RF packet format](img/rf_packet_format.png)

Ref to the BC5602 datasheet, the packet starts with preambule **10101010** and has 3-byte address, **9 bits PCF** and a dynamic payload length, but always 10 bytes.

### Payload
![Payload example](img/payload_example.png)

The payload is 10 bytes. The first byte is a command, followed by a control register where each bit corresponds to an entity.
Then comes brightness for the front lamp, two bytes of color temperature, brightness for the back lamp and the color temperature.
The payload ends with **01 02** in my case. It doesn't appear that color temperature can be changed for the back lamp, so both lamps share the same value.

#### Control register

| Bit number | Description       |
|------------|-------------------|
| 0          | On/Off            |
| 1          | Auto              |
| 2          | Favorit           |
| 3          | Back lamp on      |      
| 4          | Both lamps on     |      
| 5          | Ultrasonic sensor |     
| 6          | Not in use ?      |     
| 7          | Not in use ?      |     

#### Payload commands

* 00 - Remote control wakes up and contacts the lamp
* 02 - On/off light
* 03 - Change brightness and color temperature
* 04 - Sync lamp state
* 05 - Remote control goes to sleep and informs the lamp
* 0A - Paring mode

#### Brightness
The brightness ranges from 1% to 100%, corresponding to values from 0x01 to 0x64.

#### Color temperature
The color temperature ranges from 2700K to 6500K, corresponding from **0x0A8C** to **0x1964**.
For example, 4000K is transferred as **byte 1 = 0x0F**, **byte 2 = 0xA0**.

### Pairing process
During pairing uses address **E2 08 00 B0** for communication between remove control and the lamp.
The packet also has a 10‑byte payload, but some fields differ.
The command field is always **0x0A**, and the payload ends with bytes **01 02** in my case.
The unknown bytes appear static and may be related to communication address used in normal mode.
![Payload example](img/pairing_packet.png)

## Home Assistant
The MQTT will detect HALO 2 lamp and create entities. The lamp changes values gradually, which takes time, and it is easy to confuse it by rapidly changing the state in HA.
The On/Off switch powers off the both front- and back- lights and will try to prevent them from changing until the lamp is turned on again.
![HA MQTT device](img/home_assistant_screenshot1.png)

## Auto-ACK and No-Auto-ACK modes of BC5602
The HALO 2 remote control and the lamp operates in standard mode with the Auto-ACK feature. This means that when remote control sends an RF-packet,
the lamp automatically replies with an ACK packet. The BC5602 in the integration device behaves the same when changing og requesting status from the lamp.
The lamp itself does not report status proactively; it communicates only through ACK packets.
To track the current state, the integration device must poll the lamp every 5 seconds. At the same time, it attempts to sniff packets from the remote control.
This is more complicated, because the integration device must disable Auto-ACK; otherwise, the remote control will not reach the lamp.
Disabling Auto-ACK also disabling dynamic payload length feature and CRC, so the integration device must process the Packet Control Field and adjust the payload to one bit, since the PCF is 9 bits long.

## Unsolved part
The only unsolved question is how to convert data from the pairing payload into the communication address, or how to simplify obtaining the communication address.
BC5602 does not allow receiving all RF-traffic at a given frequency; it requires a valid address field in the packet.
**Direct mode**, which is not described in datasheet, doesn't allow this as well.
