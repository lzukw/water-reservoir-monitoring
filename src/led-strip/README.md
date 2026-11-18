# Raspberry Pi Pico für LED-Strips

Dieser Ordner ist ein Raspberry-Pi-Pico-Projekt für VSCode.

## Anleitung für Benutzer

1. Raspberry Pi Pico mit 5V versorgen (z.B. USB-Micro-Kabel und Handy-Ladegerät)

1. Verbinden mit WLAN "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" (Die XX sind die MAC-Adresse des Pico) und Passwort "1234567890"

1. Im Browser aufrufen `http://192.168.4.1`

1. Led-Anzahl und Helligkeit (0..255) einstellen und Mit Test-Button testen

1. Einen Hostnamen (unter diesem ist der Pico dann in der Fritzbox sichtbar), die WLAN-SSID und das WALN-Passwort der Fritzbox einstellen und "Save and Apply"-Button drücken. Der Pico startet neu und verbindet sich nun als Client mit dem angegebenen WLAN. Er stellt dann nicht mehr selbst das "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" bereit, sondern verbindet sich mit der angegeben Fritzbox.

   Nur wenn der Rasperry Pi Pico keine Verbindung zu diesem neuen WLAN aufbauen kann, stellt er selbst wieder das "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" bereit und man kann von vorne beginnen.

1. Für eine erneute Konfiguration: Auf der Fritzbox >> Heimnetz >> Mesh ist der Pico nach erfolgreichem Verbindungsaufbau sichtbar. Man kann ihn entweder direkt anklicken, oder seine IP-Adresse herausfinden und sich dann im Webbrowser verbinden zu `http://<ip-adresse-des-Raspberry-Pi-Pixo>`

## Verdrahtung

VBUS vom Pico (Pin Nr. 40) --> +5V des LED-Strip
GND  vom Pico (Pin Nr. 38) --> GND des LED-Strip
GPIO-Pin 28 am Pico (Pin Nr. 34) --> Signal des RGBW-Ledstrip

## Aufsetzen des Raspberry Pi Pico

1. BOOTSEL-Button gedrückt halten und Pico per USB-micro-Kabel an PC anstecken, dann Button loslassen+
1. Der Pico meldet sich als USB-Gerät
1. Richtige Firmware downloaden. Einen Raspberry Pi Pico (W) 2 erkennt man am Aufdruck auf der Platine. Ein `H` in der Version ist kein Unterschied, sondern bedeutet nur schon aufgelötete Header:

   - [Firmware für den Raspberry Pi Pico W(H)](https://micropython.org/download/rp2-pico-w/rp2-pico-w-latest.uf2)
   - [Firmware für den Raspberry Pi Pico 2 W(H)](https://micropython.org/download/RPI_PICO2_W/RPI_PICO2_W-latest.uf2)

1. die uf2-Datei auf den wie einen "USB-Stick" wirkenden Raspberry Pi Pico kopieren. Nach ein paar Sekunden startet der Pico neu und Micropython is aufgesetzt

1. Man könnte sich per picocom verbinden, aber in VSCode öffnet sich sofort die REPL. (`picocom -b 115200 /dev/ttyACMO`, die Baudrate dürfte egal sein - 9600 Baud haben auch funktioniert)

1. Mit VSCode das Filesystem am Pico öffnen (Button unten "Toggle Mpy FS")

1. Folgende Dateien müssen im Pico-Filesystem sein:

   - Ordner `lib` ...leer, könnte für upip-Module verwendet werden.
   - `boot.py`
   - `config.json`
   - `main.py`
   - Ordner `microdot` mit den Dateien folgenden vom gitrepo aus dem dortigen Ordner `src/microdot`
     - `__init__.py`
     - `microdot.py`
     - `utemplate.py`
   - Ordner `utemplate` mit den Dateien aus dem Ordner `libs/common/utemplate` aus dem gitrepo

## Microdot

Microdot ist ein von Flask inspirierter Webserver für den Raspberry Pi Pico.

- [Documentation auf Read-the-docs](https://microdot.readthedocs.io/en/latest/)
- [Github-Seite von Microdot](https://github.com/miguelgrinberg/microdot/tree/main/src/microdot)

Die 
