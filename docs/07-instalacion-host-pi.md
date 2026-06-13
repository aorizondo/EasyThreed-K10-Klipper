# 07 — Instalación del host (Raspberry Pi)

El host ejecuta el proceso Klipper (cinemática), Moonraker (API) y una interfaz web
(Mainsail o Fluidd). Lo más cómodo es instalar todo con **KIAUH**.

---

## Hardware del host

- **Raspberry Pi 3B/3B+/4/5** o **Zero 2 W** (suficiente para 2 MCU). Tarjeta microSD ≥ 16 GB clase 10.
- Fuente de **5 V / 3 A** propia y de calidad (no alimentes la Pi desde la electrónica de impresión).
- Conexión de red (Ethernet o Wi-Fi) para acceder a la web.

> Alternativa sin Pi: cualquier PC Linux (incluso un mini-PC x86) sirve de host. Klipper host corre en
> cualquier Linux; la Pi solo es lo habitual.

---

## 1. Sistema operativo

Graba **Raspberry Pi OS Lite (64-bit)** con Raspberry Pi Imager. En el imager, preconfigura:
hostname, usuario/clave, **SSH activado** y Wi-Fi. Arranca y entra por SSH:
```bash
ssh usuario@<hostname>.local
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git
```

---

## 2. KIAUH (instalador)

```bash
cd ~
git clone https://github.com/dw-0/kiauh.git
./kiauh/kiauh.sh
```
(Tienes una copia en `repos/kiauh/` para referencia.)

En el menú de KIAUH, **Install**, en este orden:
1. **Klipper** (elige Python 3; 1 instancia para empezar).
2. **Moonraker**.
3. **Mainsail** *(o Fluidd; elige una; puedes tener ambas pero comparten puerto)*.

KIAUH crea los servicios systemd (`klipper.service`, `moonraker.service`), el venv de Python, e instala
nginx para servir la web. Al terminar, abre `http://<ip-de-la-pi>/` en el navegador.

### Mainsail vs Fluidd
Ambas valen. **Mainsail** tiene UI muy pulida; **Fluidd** es algo más compacta. Para este proyecto da
igual; el repo asume Mainsail en los ejemplos. Repos: mainsail-crew/mainsail · fluidd-core/fluidd.

---

## 3. Estructura de archivos en la Pi

Tras instalar, lo relevante:
```
~/printer_data/config/printer.cfg     <-- tu config principal (edítala desde la web)
~/printer_data/config/moonraker.conf  <-- config de Moonraker
~/klipper/                            <-- código + 'make menuconfig' para firmware
~/moonraker/
```
Copia las configs de este repo (`klipper-config/`) a `~/printer_data/config/` adaptando los `serial:` y
los pines de la K10. Mantén los `include/` en una subcarpeta y referencia con `[include include/...]`.

---

## 4. Compilar y flashear firmware

Desde la Pi: `cd ~/klipper && make menuconfig && make` (ver [05 — Flasheo](05-flasheo-klipper.md) para las
opciones de cada MCU). KIAUH también ofrece `[Advanced] → [Build + Flash]`.

---

## 5. Identificar los MCU

```bash
ls /dev/serial/by-id/*
```
Apunta cada ruta y métela en `[mcu]` (CNC) y `[mcu k10]`. Reinicia Klipper: en Mainsail, botón
**FIRMWARE_RESTART**. Si conecta, ¡host listo!

---

## 6. Acelerómetro para input shaping (opcional, recomendado)

Si quieres input shaping medido (no a ojo), un **ADXL345** por SPI a la Pi:
- Habilita SPI en la Pi (`raspi-config` → Interface → SPI).
- Instala el "Linux MCU" de Klipper (proceso host como microcontrolador):
  KIAUH no lo hace solo; sigue <https://www.klipper3d.org/RPi_microcontroller.html> y
  <https://www.klipper3d.org/Measuring_Resonances.html>.
- Config y comandos en [08 — Calibración](08-calibracion.md).

---

## Servicios útiles
```bash
sudo systemctl status klipper moonraker
sudo systemctl restart klipper
journalctl -u klipper -f          # log en vivo (útil para depurar arranque)
```

Fuentes: <https://github.com/dw-0/kiauh> · <https://docs.mainsail.xyz/setup/kiauh/> ·
<https://www.klipper3d.org/Installation.html>
