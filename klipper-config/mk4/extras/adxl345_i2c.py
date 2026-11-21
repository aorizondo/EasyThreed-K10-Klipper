# ADXL345 over I2C read directly by the host (Raspberry Pi /dev/i2c-N)
#
# Stock Klipper only drives the ADXL345 over SPI. Some 10-pin boards have the
# chip's SDO tied to GND (I2C only): over SPI they answer "Invalid adxl345 id
# (got 0 vs e5)". This extra reads them over the Pi's hardware I2C instead.
#
# Wiring (Pi 40-pin header): SDA -> pin 3, SCL -> pin 5, CS -> 3.3V (selects
# I2C), SDO -> GND or loose (address 0x53; SDO high -> 0x1D), 3V3, GND.
# Host: "dtparam=i2c_arm=on,i2c_arm_baudrate=400000" in config.txt, "i2c-dev"
# in /etc/modules, klipper user in the "i2c" group.
#
# Install by symlinking this file into ~/klipper/klippy/extras/. Config:
#
#   [adxl345_i2c cabezal]
#   #i2c_bus: 1                   # /dev/i2c-1
#   #i2c_address: 83              # 0x53
#   #rate: 800                    # 25..1600 Hz
#   axes_map: x, y, z
#
# Same commands as [adxl345]: ACCELEROMETER_QUERY [CHIP=cabezal] (CHIP is
# optional while this is the only accelerometer), and
# accel_chip: adxl345_i2c cabezal in [resonance_tester].
#
# Samples are read by a separate process (no GIL contention with klippy) and
# timestamped with the host clock, so timing is less exact than the MCU-side
# SPI reader. Shaper calibration only needs the average sample rate, which is
# what matters here; Klipper docs still prefer SPI for the ADXL345.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import ctypes, logging, multiprocessing, os, queue, struct, time
from . import adxl345, bulk_sensor

REG_FIFO_STATUS = 0x39
REG_DATAX0 = 0x32
FIFO_CTL_STREAM = 0x80
DATA_FORMAT_FULL_RES_16G = 0x0B
POWER_CTL_MEASURE = 0x08
FIFO_SIZE = 32

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

class _I2CMsg(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('len', ctypes.c_uint16),
                ('buf', ctypes.POINTER(ctypes.c_uint8))]

class _I2CRdwr(ctypes.Structure):
    _fields_ = [('msgs', ctypes.POINTER(_I2CMsg)), ('nmsgs', ctypes.c_uint32)]

class I2CDevice:
    def __init__(self, bus, addr):
        self.fd = os.open("/dev/i2c-%d" % (bus,), os.O_RDWR)
        self.addr = addr
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.wbuf = (ctypes.c_uint8 * 2)()
        self.rbuf = (ctypes.c_uint8 * 8)()
        self.msgs = (_I2CMsg * 2)()
        self.rdwr = _I2CRdwr(self.msgs, 0)
    def close(self):
        os.close(self.fd)
    def _xfer(self, nmsgs):
        self.rdwr.nmsgs = nmsgs
        if self.libc.ioctl(self.fd, I2C_RDWR, ctypes.byref(self.rdwr)) < 0:
            err = ctypes.get_errno()
            raise OSError(err, "I2C transfer: %s" % (os.strerror(err),))
    def read(self, reg, count):
        # Write register + repeated start + read (the only multi-message
        # form the bcm2835 driver accepts: the read must be last)
        self.wbuf[0] = reg
        self.msgs[0] = _I2CMsg(self.addr, 0, 1, self.wbuf)
        self.msgs[1] = _I2CMsg(self.addr, I2C_M_RD, count, self.rbuf)
        self._xfer(2)
        return bytes(self.rbuf[:count])
    def write(self, reg, val):
        self.wbuf[0] = reg
        self.wbuf[1] = val & 0xFF
        self.msgs[0] = _I2CMsg(self.addr, 0, 2, self.wbuf)
        self._xfer(1)

def _reader_main(bus, addr, rate, out_q, stop_ev):
    # Child process: drain the chip FIFO and send (time, raw) batches.
    # Times use CLOCK_MONOTONIC_RAW, the same clock as the klippy reactor.
    out_q.cancel_join_thread()      # never block exit on unread batches
    try:
        os.nice(-5)
    except OSError:
        pass
    dev = I2CDevice(bus, addr)
    period = 1. / rate
    poll = min(FIFO_SIZE // 2 * period, 0.050)
    last_t = None
    try:
        while not stop_ev.is_set():
            entries = dev.read(REG_FIFO_STATUS, 1)[0] & 0x3f
            now = time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
            if not entries:
                time.sleep(period * 2.)
                continue
            raw = [dev.read(REG_DATAX0, 6) for i in range(entries)]
            # Newest entry was produced just before "now". Track the chip
            # clock with a soft correction so timestamps stay monotonic.
            times = []
            for i in range(entries):
                est = now - (entries - 1 - i) * period
                if last_t is None or abs(est - last_t - period) > 4. * period:
                    t = est
                else:
                    corr = 0.02 * (est - (last_t + period))
                    t = last_t + period + max(-.5 * period,
                                              min(.5 * period, corr))
                times.append(t)
                last_t = t
            out_q.put((times, b''.join(raw), entries >= FIFO_SIZE))
            time.sleep(poll)
    except Exception as e:
        out_q.put(("error", str(e)))
    finally:
        dev.close()

class ADXL345_I2C:
    def __init__(self, config):
        self.printer = config.get_printer()
        cmd_helper = adxl345.AccelCommandHelper(config, self)
        # Also answer ACCELEROMETER_QUERY etc. without CHIP= unless another
        # accelerometer already took the default
        try:
            cmd_helper.register_commands(None)
        except config.error:
            pass
        self.axes_map = adxl345.read_axes_map(
            config, adxl345.SCALE_XY, adxl345.SCALE_XY, adxl345.SCALE_Z)
        self.data_rate = config.getint('rate', 800)
        if (self.data_rate not in adxl345.QUERY_RATES
                or self.data_rate > 1600):
            raise config.error("Invalid rate parameter: %d" % (self.data_rate,))
        self.i2c_bus = config.getint('i2c_bus', 1, minval=0)
        self.i2c_addr = config.getint('i2c_address', 0x53, minval=0,
                                      maxval=127)
        self.dev = None
        self.proc = self.out_q = self.stop_ev = None
        self.error_count = self.overflows = 0
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch, self._start_measurements,
            self._finish_measurements, adxl345.BATCH_UPDATES)
        self.name = config.get_name().split()[-1]
        hdr = ('time', 'x_acceleration', 'y_acceleration', 'z_acceleration')
        self.batch_bulk.add_mux_endpoint("adxl345/dump_adxl345", "sensor",
                                         self.name, {'header': hdr})
    def _get_dev(self):
        if self.dev is None:
            try:
                self.dev = I2CDevice(self.i2c_bus, self.i2c_addr)
            except OSError as e:
                raise self.printer.command_error(
                    "ADXL345 I2C: cannot open /dev/i2c-%d: %s"
                    % (self.i2c_bus, e))
        return self.dev
    def read_reg(self, reg):
        try:
            return self._get_dev().read(reg, 1)[0]
        except OSError as e:
            raise self.printer.command_error(
                "ADXL345 I2C read failed (address 0x%02x): %s. Check wiring "
                "(SDA pin 3, SCL pin 5, CS to 3.3V)." % (self.i2c_addr, e))
    def set_reg(self, reg, val, minclock=0):
        try:
            self._get_dev().write(reg, val)
        except OSError as e:
            raise self.printer.command_error(
                "ADXL345 I2C write failed: %s" % (e,))
        stored_val = self.read_reg(reg)
        if stored_val != val:
            raise self.printer.command_error(
                "Failed to set ADXL345 register [0x%x] to 0x%x: got 0x%x"
                % (reg, val, stored_val))
    def start_internal_client(self):
        aqh = adxl345.AccelQueryHelper(self.printer)
        self.batch_bulk.add_client(aqh.handle_batch)
        return aqh
    # Start, stop, and process message batches
    def _start_measurements(self):
        dev_id = self.read_reg(adxl345.REG_DEVID)
        if dev_id != adxl345.ADXL345_DEV_ID:
            raise self.printer.command_error(
                "Invalid adxl345 id (got %x vs %x)."
                % (dev_id, adxl345.ADXL345_DEV_ID))
        self.set_reg(adxl345.REG_POWER_CTL, 0x00)
        self.set_reg(adxl345.REG_DATA_FORMAT, DATA_FORMAT_FULL_RES_16G)
        self.set_reg(adxl345.REG_FIFO_CTL, 0x00)
        self.set_reg(adxl345.REG_BW_RATE, adxl345.QUERY_RATES[self.data_rate])
        self.set_reg(adxl345.REG_FIFO_CTL, FIFO_CTL_STREAM)
        ctx = multiprocessing.get_context('fork')
        self.out_q = ctx.Queue()
        self.stop_ev = ctx.Event()
        self.proc = ctx.Process(
            target=_reader_main, args=(self.i2c_bus, self.i2c_addr,
                                       self.data_rate, self.out_q,
                                       self.stop_ev))
        self.proc.daemon = True
        self.proc.start()
        self.set_reg(adxl345.REG_POWER_CTL, POWER_CTL_MEASURE)
        self.error_count = self.overflows = 0
        logging.info("ADXL345 I2C starting '%s' measurements at %d Hz",
                     self.name, self.data_rate)
    def _finish_measurements(self):
        if self.proc is not None:
            self.stop_ev.set()
            self.proc.join(1.)
            if self.proc.is_alive():
                self.proc.terminate()
            self.proc = None
        try:
            self.set_reg(adxl345.REG_POWER_CTL, 0x00)
        finally:
            logging.info("ADXL345 I2C finished '%s' measurements", self.name)
    def _process_batch(self, eventtime):
        if self.out_q is None:
            return {}
        mcu = self.printer.lookup_object('mcu')
        (x_pos, x_scale), (y_pos, y_scale), (z_pos, z_scale) = self.axes_map
        samples = []
        while True:
            try:
                item = self.out_q.get_nowait()
            except queue.Empty:
                break
            if item[0] == "error":
                raise self.printer.command_error(
                    "ADXL345 I2C reader failed: %s" % (item[1],))
            times, raw, overflow = item
            self.overflows += overflow
            for i, t in enumerate(times):
                raw_xyz = struct.unpack_from('<hhh', raw, i * 6)
                samples.append((
                    round(mcu.estimated_print_time(t), 6),
                    round(raw_xyz[x_pos] * x_scale, 6),
                    round(raw_xyz[y_pos] * y_scale, 6),
                    round(raw_xyz[z_pos] * z_scale, 6)))
        if not samples:
            return {}
        return {'data': samples, 'errors': self.error_count,
                'overflows': self.overflows}

def load_config(config):
    return ADXL345_I2C(config)

def load_config_prefix(config):
    return ADXL345_I2C(config)
