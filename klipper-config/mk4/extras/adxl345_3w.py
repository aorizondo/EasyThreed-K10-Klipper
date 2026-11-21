# ADXL345 over 3-wire SPI (data in and out on the SDA/SDIO pin)
#
# For ADXL345 boards hard-wired for I2C: the chip's SDO pin is tied to GND on
# the PCB, so in normal 4-wire SPI nothing comes back on MISO ("Invalid
# adxl345 id (got 0 vs e5)") even though I2C works. In 3-wire mode the chip
# answers on SDA, so it is wired as:
#
#   Pi MOSI (GPIO10, pin 19) --[1k]--+-- module SDA
#   Pi MISO (GPIO9,  pin 21) --------+
#   module SDO: not connected        SCL, CS, 3V3, GND: as usual
#
# The resistor lets the chip override MOSI while it is sending data.
#
# Install by symlinking this file into ~/klipper/klippy/extras/. Config is the
# same as [adxl345], only the section name changes:
#
#   [adxl345_3w cabezal]
#   cs_pin: rpi:None
#   spi_bus: spidev0.0
#   spi_speed: 2000000            # the resistor slows the SDA edges
#   axes_map: x, y, z
#
# Commands and references are unchanged: ACCELEROMETER_QUERY CHIP=cabezal,
# accel_chip: adxl345_3w cabezal, CHIPS="adxl345_3w cabezal".
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import adxl345

DATA_FORMAT_SPI_3WIRE = 0x40

class ADXL345_3W(adxl345.ADXL345):
    def set_reg(self, reg, val, minclock=0):
        # Klipper writes DATA_FORMAT=0x0B (4-wire): keep the 3-wire bit set
        if reg == adxl345.REG_DATA_FORMAT:
            val |= DATA_FORMAT_SPI_3WIRE
        adxl345.ADXL345.set_reg(self, reg, val, minclock)
    def _start_measurements(self):
        # After power-up the chip is in 4-wire mode and the DEVID read would
        # return 0: switch it first (writes only need SDA, so they work).
        self.spi.spi_send([adxl345.REG_DATA_FORMAT, DATA_FORMAT_SPI_3WIRE])
        adxl345.ADXL345._start_measurements(self)

def load_config(config):
    return ADXL345_3W(config)

def load_config_prefix(config):
    return ADXL345_3W(config)
