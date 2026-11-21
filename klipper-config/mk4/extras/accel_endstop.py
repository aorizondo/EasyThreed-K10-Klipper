# EXPERIMENTAL: X/Y sensorless homing by crash detection with an ADXL345
#
# The ADXL345 compares each sample against a threshold (AC coupled, one chip
# axis at a time) and raises INT1 on a crash. INT1 is wired to a host GPIO
# declared as an endstop on the "rpi" MCU, so Klipper stops the steppers
# through a normal multi-MCU homing move (trsync), not through I2C polling.
# The interrupt is only enabled once the move is cruising ("blank_time"), so
# the motor start jerk does not trigger it. The crash point (carriage against
# the frame) is taken as position_endstop of [stepper_x]/[stepper_y].
#
# Wiring: ADXL345 INT1 -> Pi pin 11 (GPIO17). INT1 is push-pull, active high.
# Needs an accelerometer object with read_reg/set_reg/axes_map
# (extras/adxl345_i2c.py). Install by symlinking into ~/klipper/klippy/extras/.
#
#   [accel_endstop]
#   accel_chip: adxl345_i2c cabezal
#   int_pin: rpi:gpio17
#   #threshold: 3.0       # g, 0.0625 steps (default for both axes)
#   #threshold_x:         # per-axis override
#   #threshold_y:
#   #speed: 15            # mm/s towards the frame
#   #blank_time: 0.1      # s after the move starts before arming INT1
#   #backoff: 5           # mm moved away from the frame before homing
#   #retract: 3           # mm moved away after the crash
#   #overtravel: 6        # mm past position_endstop before giving up
#
# HOME_ACCEL [AXIS=X|Y]          home X then Y (or one axis)
# ACCEL_CRASH_TEST AXIS=X|Y [SAMPLES=5]
#   repeatability: HOME_ACCEL, then crash again from position_endstop+15 and
#   report where it triggers (ideal: +0.000 every time).
# ACCEL_NOISE_TEST AXIS=X|Y START= END= [SPEED=] [THRESHOLDS=1.5,2,3,...]
#   free move (no crash) per threshold: the lowest "clean" one is usable.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math
from . import homing

REG_THRESH_ACT = 0x24
REG_ACT_INACT_CTL = 0x27
REG_BW_RATE = 0x2C
REG_POWER_CTL = 0x2D
REG_INT_ENABLE = 0x2E
REG_INT_MAP = 0x2F
REG_INT_SOURCE = 0x30
REG_DATA_FORMAT = 0x31
REG_FIFO_CTL = 0x38
INT_ACTIVITY = 0x10
ACT_AC_COUPLED = 0x80
ACT_AXIS_BITS = (0x40, 0x20, 0x10)      # chip X, Y, Z
BW_RATE_3200HZ = 0x0F
DATA_FORMAT_FULL_RES_16G = 0x0B         # INT active high
THRESH_G_PER_LSB = 0.0625

class AccelEndstop:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.chip_name = config.get('accel_chip')
        ppins = self.printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('int_pin'))
        threshold = config.getfloat('threshold', 3., minval=0.0625,
                                    maxval=15.9)
        self.thresholds = {
            a: config.getfloat('threshold_' + a, threshold, minval=0.0625,
                               maxval=15.9) for a in 'xy'}
        self.speed = config.getfloat('speed', 15., above=0., maxval=50.)
        self.blank_time = config.getfloat('blank_time', 0.1, minval=0.,
                                          maxval=1.)
        self.backoff = config.getfloat('backoff', 5., minval=0.)
        self.retract = config.getfloat('retract', 3., minval=0.)
        self.overtravel = config.getfloat('overtravel', 6., above=0.,
                                          maxval=20.)
        self.chip = None
        self.arm_timer = None
        self.printer.register_event_handler('klippy:mcu_identify',
                                            self._handle_mcu_identify)
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('HOME_ACCEL', self.cmd_HOME_ACCEL,
                               desc=self.cmd_HOME_ACCEL_help)
        gcode.register_command('ACCEL_CRASH_TEST', self.cmd_ACCEL_CRASH_TEST,
                               desc=self.cmd_ACCEL_CRASH_TEST_help)
        gcode.register_command('ACCEL_NOISE_TEST', self.cmd_ACCEL_NOISE_TEST,
                               desc=self.cmd_ACCEL_NOISE_TEST_help)
    def _handle_mcu_identify(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('x') or stepper.is_active_axis('y'):
                self.mcu_endstop.add_stepper(stepper)
    def _handle_connect(self):
        self.chip = self.printer.lookup_object(self.chip_name, None)
        if self.chip is None or not hasattr(self.chip, 'set_reg'):
            raise self.printer.config_error(
                "accel_endstop: '%s' is not a usable accelerometer"
                % (self.chip_name,))
    def _get_threshold(self, gcmd, axis_name):
        return gcmd.get_float('THRESHOLD', self.thresholds[axis_name],
                              minval=0.0625, maxval=15.9)
    def _pause(self, secs):
        self.reactor.pause(self.reactor.monotonic() + secs)
    # Chip setup: measure at 3200 Hz with activity detection ready, INT off
    def _prepare(self, axis, threshold):
        chip = self.chip
        chip_axis = chip.axes_map[axis][0]
        thresh = max(1, min(255, int(round(threshold / THRESH_G_PER_LSB))))
        chip.set_reg(REG_INT_ENABLE, 0x00)
        chip.set_reg(REG_POWER_CTL, 0x00)
        chip.set_reg(REG_BW_RATE, BW_RATE_3200HZ)
        chip.set_reg(REG_DATA_FORMAT, DATA_FORMAT_FULL_RES_16G)
        chip.set_reg(REG_FIFO_CTL, 0x00)
        chip.set_reg(REG_THRESH_ACT, thresh)
        chip.set_reg(REG_ACT_INACT_CTL,
                     ACT_AC_COUPLED | ACT_AXIS_BITS[chip_axis])
        chip.set_reg(REG_INT_MAP, 0x00)
        chip.set_reg(REG_POWER_CTL, 0x08)
        self._pause(0.020)
        chip.read_reg(REG_INT_SOURCE)
        return thresh * THRESH_G_PER_LSB, "xyz"[chip_axis]
    def _enable_int(self):
        # AC coupling takes its reference when activity detection starts
        self.chip.read_reg(REG_INT_SOURCE)
        self.chip.set_reg(REG_INT_ENABLE, INT_ACTIVITY)
    def _disarm(self):
        if self.arm_timer is not None:
            self.reactor.unregister_timer(self.arm_timer)
            self.arm_timer = None
        try:
            self.chip.set_reg(REG_INT_ENABLE, 0x00)
            self.chip.set_reg(REG_POWER_CTL, 0x00)
            self.chip.read_reg(REG_INT_SOURCE)
        except self.printer.command_error as e:
            logging.warning("accel_endstop: disarm failed: %s", e)
    def _schedule_enable(self, start_print_time):
        mcu = self.mcu_endstop.get_mcu()
        arm_at = start_print_time + self.blank_time
        def arm_cb(eventtime):
            if mcu.estimated_print_time(eventtime) < arm_at:
                return eventtime + 0.002
            try:
                self._enable_int()
            except self.printer.command_error as e:
                logging.warning("accel_endstop: arming failed: %s", e)
            self.arm_timer = None
            return self.reactor.NEVER
        self.arm_timer = self.reactor.register_timer(arm_cb,
                                                     self.reactor.NOW)
    # Crash towards the frame; returns the trigger coordinate, where the
    # toolhead coordinate of the axis is shifted by "shift" so the target
    # (position_endstop - overtravel) stays inside the kinematic limits.
    def _crash(self, axis, target, speed, threshold, check_triggered=True):
        toolhead = self.printer.lookup_object('toolhead')
        thresh_g, chip_axis = self._prepare(axis, threshold)
        try:
            pos = toolhead.get_position()
            movepos = list(pos)
            movepos[axis] = target
            hmove = homing.HomingMove(
                self.printer, [(self.mcu_endstop, "accel_endstop")])
            self._schedule_enable(toolhead.get_last_move_time())
            trig = hmove.homing_move(movepos, speed, probe_pos=True,
                                     check_triggered=check_triggered)
        finally:
            self._disarm()
        return trig[axis], thresh_g, chip_axis
    cmd_ACCEL_NOISE_TEST_help = (
        "EXPERIMENTAL: find the lowest threshold a free move does not trip")
    def cmd_ACCEL_NOISE_TEST(self, gcmd):
        # Axis must be homed (HOME_ACCEL, even if approximate); START/END
        # must be well inside the travel: this move should NOT trigger.
        axis_name = gcmd.get('AXIS').lower()
        if axis_name not in ('x', 'y'):
            raise gcmd.error("AXIS must be X or Y")
        axis = "xyz".index(axis_name)
        start = gcmd.get_float('START')
        end = gcmd.get_float('END')
        speed = gcmd.get_float('SPEED', self.speed, above=0., maxval=80.)
        thresholds = [float(t) for t in
                      gcmd.get('THRESHOLDS', '1.5,2,3,4,6,8').split(',')]
        toolhead = self.printer.lookup_object('toolhead')
        for thr in thresholds:
            pos = toolhead.get_position()
            pos[axis] = start
            toolhead.manual_move(pos, 50.)
            toolhead.wait_moves()
            trig, thresh_g, chip_axis = self._crash(
                axis, end, speed, thr, check_triggered=False)
            if abs(trig - end) > 0.01:
                gcmd.respond_info("%.2f g: FALSE TRIGGER at %s=%.2f (%.1f mm"
                                  " into the move)" % (
                                      thresh_g, axis_name.upper(), trig,
                                      abs(trig - start)))
            else:
                gcmd.respond_info("%.2f g: clean (%.0f mm at %.0f mm/s)"
                                  % (thresh_g, abs(end - start), speed))
    def _axis_params(self, axis_name):
        settings = self.printer.lookup_object('configfile').get_status(
            None)['settings']['stepper_' + axis_name]
        return (settings['position_endstop'], settings['position_min'],
                settings['position_max'])
    def _home_axis(self, gcmd, axis_name, speed, threshold):
        axis = "xyz".index(axis_name)
        pe, pmin, pmax = self._axis_params(axis_name)
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        # Unknown position: pretend we are mid-travel, back off, then crash
        # over more than the full travel.
        pos = toolhead.get_position()
        pos[axis] = (pmin + pmax) / 2.
        toolhead.set_position(pos, homing_axes=axis_name)
        try:
            if self.backoff:
                pos[axis] += self.backoff
                toolhead.manual_move(pos, 50.)
                toolhead.wait_moves()
            start = pe + (pmax - pmin) * 1.2 + self.overtravel
            pos[axis] = start
            toolhead.set_position(pos, homing_axes=axis_name)
            trig, thresh_g, chip_axis = self._crash(axis, pe, speed,
                                                    threshold)
        except:
            toolhead.get_kinematics().clear_homing_state(axis_name)
            raise
        # trig is in shifted coordinates (real = shifted - overtravel); the
        # crash point is position_endstop by definition.
        halt = toolhead.get_position()
        overshoot = halt[axis] - trig
        halt[axis] = pe + overshoot
        toolhead.set_position(halt, homing_axes=axis_name)
        if self.retract:
            halt[axis] = pe + self.retract
            toolhead.manual_move(halt, 50.)
        gcmd.respond_info(
            "HOME_ACCEL %s: crash detected after %.2f mm (threshold %.2f g on"
            " chip %s), position set to %.3f"
            % (axis_name.upper(), start - trig, thresh_g, chip_axis, pe))
    cmd_HOME_ACCEL_help = "EXPERIMENTAL: home X/Y by crash detection (ADXL345)"
    def cmd_HOME_ACCEL(self, gcmd):
        axes = gcmd.get('AXIS', 'XY').lower()
        speed = gcmd.get_float('SPEED', self.speed, above=0., maxval=50.)
        for axis_name in 'xy':
            if axis_name in axes:
                self._home_axis(gcmd, axis_name, speed,
                                self._get_threshold(gcmd, axis_name))
    cmd_ACCEL_CRASH_TEST_help = (
        "EXPERIMENTAL: repeatability of the accelerometer crash detection")
    def cmd_ACCEL_CRASH_TEST(self, gcmd):
        axis_name = gcmd.get('AXIS').lower()
        if axis_name not in ('x', 'y'):
            raise gcmd.error("AXIS must be X or Y")
        axis = "xyz".index(axis_name)
        samples = gcmd.get_int('SAMPLES', 5, minval=1, maxval=20)
        speed = gcmd.get_float('SPEED', self.speed, above=0., maxval=50.)
        threshold = self._get_threshold(gcmd, axis_name)
        pe, pmin, pmax = self._axis_params(axis_name)
        toolhead = self.printer.lookup_object('toolhead')
        self._home_axis(gcmd, axis_name, speed, threshold)
        results = []
        for i in range(samples):
            pos = toolhead.get_position()
            pos[axis] = pe + 15.
            toolhead.manual_move(pos, 50.)
            toolhead.wait_moves()
            # Shift so the target past the frame stays inside the limits
            shift = self.overtravel
            pos[axis] += shift
            toolhead.set_position(pos)
            try:
                trig, thresh_g, chip_axis = self._crash(
                    axis, pe + shift - self.overtravel, speed, threshold)
            except:
                # Position is shifted and steps were probably lost
                toolhead.get_kinematics().clear_homing_state(axis_name)
                raise
            real = toolhead.get_position()
            real[axis] -= shift
            toolhead.set_position(real)
            results.append(trig - shift - pe)
            gcmd.respond_info("Sample %d: triggered at %+.3f mm vs %s"
                              " home" % (i + 1, results[-1],
                                         axis_name.upper()))
            pos = toolhead.get_position()
            pos[axis] = pe + self.retract
            toolhead.manual_move(pos, 50.)
        mean = sum(results) / len(results)
        sd = math.sqrt(sum((r - mean) ** 2 for r in results) / len(results))
        gcmd.respond_info(
            "ACCEL_CRASH_TEST %s: %d samples at %.0f mm/s, threshold %.2f g:"
            " mean %+.3f, range %.3f, stddev %.3f mm (skipped steps at the"
            " frame are not seen; re-home with HOME_ACCEL after this test)"
            % (axis_name.upper(), len(results), speed, threshold, mean,
               max(results) - min(results), sd))

def load_config(config):
    return AccelEndstop(config)
