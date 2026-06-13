# 04 — Comunicar la K10 con el host (USB nativo / UART)

> El USB-C de la K10 **no lleva datos al MCU** (es solo USB-PD de alimentación). Hay tres formas de
> darle a Klipper un enlace con la K10. Todas a **3.3 V** (nivel del GD32). Diagramas y esquema de
> soldadura: [diagrams/k10-uart-conexion.md](../diagrams/k10-uart-conexion.md) y
> [diagrams/k10-soldadura-usb.md](../diagrams/k10-soldadura-usb.md).

## Resumen de las tres vías

| Vía                             | Qué soldar                                                           | Compras             | Resultado en la Pi                  |
|---------------------------------|----------------------------------------------------------------------|---------------------|-------------------------------------|
| **A. USB nativo** (recomendada) | PA12(D+), PA11(D−), GND + **R 1.5 kΩ** de D+ a 3.3 V, a un cable USB | nada (un cable USB) | `/dev/serial/by-id/usb-Klipper_...` |
| **B. UART por GPIO de la Pi**   | PA9(TX), PA10(RX), GND a la cabecera GPIO de la Pi                   | nada                | `/dev/ttyAMA0`                      |
| **C. UART + adaptador USB-TTL** | PA9, PA10, GND al adaptador (3.3 V)                                  | adaptador (~3–6 €)  | `/dev/serial/by-id/...`             |

---

## A. USB nativo del GD32 (recomendada)

El GD32F303 tiene **USB nativo**: **PA12 = D+**, **PA11 = D−** (igual que STM32F103). El reversing
confirmó que esa función está presente pero sin usar. Llevándola a un cable USB obtienes un dispositivo
Klipper USB de verdad, sin adaptador.

- **Imprescindible**: resistencia de **1.5 kΩ de PA12 (D+) a 3.3 V**. El GD32/STM32F103 **no** tiene
  pull-up interna; sin ella el USB **no enumera**.
- **VBUS (+5V, hilo rojo del USB): NO conectar** al MCU (la K10 se alimenta por su DC).
- **GND común**: el hilo negro del USB une masas. **D+/D− cortos y trenzados.**
- Klipper: interfaz de comunicación **USB (PA11/PA12)**.

👉 **Esquema eléctrico de soldadura completo, colores de cable y cómo localizar PA11/PA12 en el chip:
[diagrams/k10-soldadura-usb.md](../diagrams/k10-soldadura-usb.md).**

Fuentes: <https://klipper.discourse.group/t/klipper-support-on-stm32f103-over-usb/25074> ·
<https://klipper.discourse.group/t/support-for-new-creality-boards-4-2-2-with-gd32f303/3016>

---

## B. UART por GPIO de la Raspberry Pi (sin comprar nada)

Usa una USART del GD32 (USART1: **PA9 = TX**, **PA10 = RX**) contra el UART hardware de la Pi.

```text
   K10 (GD32, 3.3 V)            Raspberry Pi (cabecera GPIO, 3.3 V)
   PA9  (TX)  ----------------> GPIO15 / RXD   (pin físico 10)
   PA10 (RX)  <---------------- GPIO14 / TXD   (pin físico 8)
   GND        -----------------  GND           (pin físico 6)
```

- Regla: **TX de un lado → RX del otro** (cruzado). Si no conecta, intercambia los dos cables de datos.
- En la Pi: `sudo raspi-config` → Interface → Serial → consola por serie **NO**, hardware serial **SÍ**. Reinicia.
- Klipper: comunicación **Serial (USART1, PA9/PA10)**. En `[mcu k10]`: `serial: /dev/ttyAMA0` (o `/dev/serial0`).
- 3.3 V nativo en ambos lados; **no** uses los pines de 5 V de la Pi para señal.

---

## C. UART + adaptador USB-TTL (si prefieres por USB y ya tienes adaptador)

Igual que B pero a un adaptador FTDI/CP2102/CH340 **en modo 3.3 V**, que va por USB a la Pi.
Mismo cruce TX↔RX, GND común, **VCC del adaptador sin conectar**. Aparece como `/dev/serial/by-id/...`.

> Un adaptador en 5 V puede dañar el GD32. Si no tienes adaptador y el host es una Pi, usa la **A** o la **B**.

---

## ¿Cuál elegir?

- **A (USB nativo)** es la más limpia (USB real) y solo añade una resistencia de 1.5 kΩ. Es la opción
  recomendada.
- **B (UART por GPIO)** es el plan B sin comprar ni soldar D+/D−: 3 cables a la Pi.
- **C** solo si ya tienes un adaptador USB-TTL 3.3 V o el host no es una Pi.

En las tres: localiza los pines con multímetro + [docs/10](10-reversing-pinout-k10.md), usa AWG30, fija con
kapton (alivio de tensión), y **masa común** siempre.

---

## Seguridad (común a todas)

- **3.3 V** en todas las señales hacia el GD32. Nada de 5 V en D+/D−/UART/ADC.
- **GND común** entre K10, Pi y/o adaptador.
- Alimentaciones de potencia (motores, calefactor) **separadas** de la lógica.
- Antes de soldar, placa **sin alimentar**, verifica cada pad con continuidad.

---

## Checklist de validación de comms

- [ ] Klipper flasheado en la K10 (ver [05](05-flasheo-klipper.md)) con la interfaz correcta (USB o Serial).
- [ ] (A) pull-up 1.5 kΩ en D+ puesta · (B) `raspi-config` con hardware serial activado.
- [ ] `ls /dev/serial/by-id/*` (A/C) o `/dev/ttyAMA0` (B) presente.
- [ ] `serial:` de `[mcu k10]` apunta a la ruta correcta.
- [ ] `FIRMWARE_RESTART` sin "mcu 'k10': Unable to connect".
- [ ] Si falla UART: intercambia TX/RX; confirma baud; 3.3 V; GND común.
- [ ] Si falla USB: revisa la pull-up de 1.5 kΩ y que D+/D− no estén cambiados.
