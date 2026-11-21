# DHT11/DHT22 ambient sensor read through the Linux kernel "dht11" IIO driver
#
# Host setup (Raspberry Pi): add "dtoverlay=dht11,gpiopin=4" to
# /boot/firmware/config.txt and reboot; install by symlinking this file into
# ~/klipper/klippy/extras/. Config:
#
#   [dht_iio]                     # must appear before the sensors using it
#   [temperature_sensor ambiente]
#   sensor_type: DHT_IIO
#   #sensor_path: /sys/bus/iio/devices/iio:device0   # default: autodetect
#   [temperature_sensor humedad]
#   sensor_type: DHT_IIO
#   dht_value: humidity           # reports %RH on the temperature graph
#
# Read errors are normal on DHT sensors: they are counted and skipped, never
# turned into a shutdown (this is an ambient sensor, not a heater guard).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import glob, logging, os, threading, time

REPORT_TIME = 2.
READ_INTERVAL = 3.  # DHT22 needs >= 2s between conversions
STALE_TIME = 60.
IIO_GLOB = "/sys/bus/iio/devices/iio:device*"

def find_dht_device():
    for dev in sorted(glob.glob(IIO_GLOB)):
        try:
            with open(os.path.join(dev, "name")) as f:
                if f.read().strip().startswith("dht11"):
                    return dev
        except (IOError, OSError):
            continue
    return None

# Sysfs reads block while the kernel bit-bangs the sensor, so they run in a
# background thread (one per device) and never on the reactor thread
class DHTReader:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.temp = self.humidity = None
        self.last_ok = 0.
        self.errors = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="dht_iio")
        self.thread.daemon = True
    def start(self):
        if not self.thread.is_alive():
            self.thread.start()
    def stop(self):
        self.stop_event.set()
    def _read_milli(self, attr):
        with open(os.path.join(self.path, attr)) as f:
            return float(f.read()) / 1000.
    def _run(self):
        while not self.stop_event.is_set():
            try:
                # The driver caches a conversion for ~2s: both reads share it
                temp = self._read_milli("in_temp_input")
                humidity = self._read_milli("in_humidityrelative_input")
            except (IOError, OSError, ValueError):
                with self.lock:
                    self.errors += 1
            else:
                with self.lock:
                    self.temp, self.humidity = temp, humidity
                    self.last_ok = time.monotonic()
            self.stop_event.wait(READ_INTERVAL)
    def get_sample(self):
        with self.lock:
            return self.temp, self.humidity, self.last_ok, self.errors

class DHTSensor:
    def __init__(self, config, reader):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.report_humidity = config.getchoice(
            'dht_value', {'temperature': False, 'humidity': True},
            'temperature')
        self.reader = reader
        self.temp = self.humidity = 0.
        self.min_temp = self.max_temp = 0.
        self._callback = None
        self.warned_stale = False
        self.printer.add_object("dht_iio " + self.name, self)
        self.sample_timer = self.reactor.register_timer(self._sample)
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
    def _handle_connect(self):
        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)
    def setup_minmax(self, min_temp, max_temp):
        self.min_temp = min_temp
        self.max_temp = max_temp
    def setup_callback(self, cb):
        self._callback = cb
    def get_report_time_delta(self):
        return REPORT_TIME
    def _sample(self, eventtime):
        temp, humidity, last_ok, errors = self.reader.get_sample()
        if temp is None:
            return eventtime + REPORT_TIME
        self.temp, self.humidity = temp, humidity
        stale = time.monotonic() - last_ok > STALE_TIME
        if stale and not self.warned_stale:
            logging.warning("dht_iio %s: no valid reading for %.0fs (%d errors)",
                            self.name, STALE_TIME, errors)
        self.warned_stale = stale
        value = humidity if self.report_humidity else temp
        mcu = self.printer.lookup_object('mcu')
        self._callback(mcu.estimated_print_time(eventtime), value)
        return eventtime + REPORT_TIME
    def get_status(self, eventtime):
        return {
            'temperature': round(self.temp, 2),
            'humidity': round(self.humidity, 1),
            'read_errors': self.reader.get_sample()[3],
        }

class DHTManager:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.readers = {}
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("klippy:disconnect",
                                            self._handle_disconnect)
        pheaters = self.printer.load_object(config, "heaters")
        pheaters.add_sensor_factory("DHT_IIO", self._create_sensor)
    def _create_sensor(self, config):
        path = config.get('sensor_path', None)
        if path is None:
            path = find_dht_device()
            if path is None:
                raise config.error(
                    "dht_iio: no 'dht11' IIO device found"
                    " (missing dtoverlay=dht11,gpiopin=N?)")
        if path not in self.readers:
            self.readers[path] = DHTReader(path)
        return DHTSensor(config, self.readers[path])
    def _handle_connect(self):
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        for reader in self.readers.values():
            reader.start()
    def _handle_disconnect(self):
        # RESTART reloads the config in the same process: stop old threads
        for reader in self.readers.values():
            reader.stop()

def load_config(config):
    return DHTManager(config)
