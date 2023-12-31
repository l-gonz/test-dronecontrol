import asyncio
import cv2
import traceback
import math
from mavsdk.offboard import PositionNedYaw
from mavsdk.action import ActionError
from mediapipe.python.solution_base import SolutionBase

from dronecontrol.common import utils, input
from dronecontrol.follow.controller import Controller
from dronecontrol.follow.follow import Follow
from dronecontrol.common.pilot import System


class TunePIDController:

    START_POS = PositionNedYaw(0, 0, -2.5, 7) # Start position at (0,0,0) in AirSim
    NEXT_VALUE_DELAY = 12

    def __init__(self, tune_yaw=True, manual=False, sample_time=20, kp_values=[], ki_values=[], kd_values=[]):
        self.log = utils.make_stdout_logger(__name__)
        self.input_handler = input.InputHandler()
        self.follow = Follow(port=14550, simulator_ip="")

        self.follow.is_follow_on = True
        self.follow.is_keyboard_control_on = False
        self.follow.pilot.offboard_poll_time = -1
        self.first_time = False

        self.sample_time = sample_time
        self.compare_velocities = True
        self.manual = manual or (kp_values == [0] and ki_values == [0] and kd_values == [0])
            
        self.save_parameter_values([kp_values, ki_values, kd_values])
        self.tune_yaw = tune_yaw
        if self.tune_yaw:
            self.follow.controller.yaw_pid.tunings = (self.kp_values.pop(0), self.ki_values.pop(0), self.kd_values.pop(0))
            self.follow.controller.fwd_pid.tunings = (0, 0, 0)
        else:
            self.follow.controller.fwd_pid.tunings = (self.kp_values.pop(0), self.ki_values.pop(0), self.kd_values.pop(0))
            self.follow.controller.yaw_pid.tunings = (0, 0, 0)


    async def run(self):
        try:
            await self.follow.pilot.connect()
        except Exception as e:
            self.log.error(e)
            return

        await asyncio.sleep(5)
        await self.follow.pilot.takeoff()
        await asyncio.sleep(2)
        self.follow.subscribe_to_image(self.on_new_image)

        self.input_data = []
        self.output_data = []
        self.pilot_pos_data = []
        self.pilot_vel_data = []
        self.pilot_time = []
        self.time = []

        await self.follow.pilot.start_offboard()
        await self.follow.pilot.set_position_ned_yaw(self.START_POS)
        await asyncio.sleep(3)
        try:
            await self.follow.run()
        except Exception as e:
            self.log.error(e)
            traceback.print_exc()


    async def on_new_image(self, p1, p2):
        if not self.manual:
            # Wait until measures have been taken for sample_time seconds and save the data
            time_data = self.follow.controller.get_time_data(True)
            if time_data and self.follow.is_follow_on and time_data[-1] > self.sample_time:
                await self.go_to_next_value(time_data)

        # Manual keyboard control
        key = cv2.waitKey(1)
        if key == ord(' '):
            await self.verify_tuning()
        else:
            await self.keyboard_control(key)


    async def go_to_next_value(self, time_data):
        # Save last run
        if not self.first_time:
            pid_data = self.follow.controller.get_yaw_data() if self.tune_yaw else self.follow.controller.get_fwd_data()
            self.input_data.append(pid_data[1])
            self.output_data.append(pid_data[2])
            pilot_data = self.follow.get_pilot_telemetry()
            self.pilot_time.append(pilot_data[0])
            self.pilot_pos_data.append(pilot_data[1])
            self.pilot_vel_data.append(pilot_data[2])
            self.time.append(time_data)

        # Reset position
        self.follow.is_follow_on = False
        await self.follow.pilot.set_position_ned_yaw(self.START_POS)

        # Set next value on the controller, repeat first one for more consistent results
        if self.first_time:
            self.first_time = False
        else:
            self.set_next_value()

        # Engage controller again in 10 seconds
        asyncio.create_task(self.restart_control(self.NEXT_VALUE_DELAY))


    async def verify_tuning(self):
        self.follow.log_measures()
        time = self.follow.controller.get_time_data()
        if Controller.is_pid_on(self.follow.controller.yaw_pid):
            yaw_input = self.follow.controller.get_yaw_data()
            self.log.info(f"Yaw parameters: {self.follow.controller.yaw_pid.tunings}")
            utils.plot(time, yaw_input, subplots=[2,1], block=False, title="Yaw data", ylabel=["H. distance", "Velocity"])
            utils.plot(time, self.follow.controller.get_yaw_output_detailed(), 
                block=False, title="Yaw output", ylabel="Velocity", legend=["p", "i", "d"])
        elif Controller.is_pid_on(self.follow.controller.fwd_pid):
            fwd_input = self.follow.controller.get_fwd_data()
            self.log.info(f"Fwd parameters: {self.follow.controller.fwd_pid.tunings}")
            utils.plot(time, fwd_input, subplots=[2,1], block=False, title="Fwd input", ylabel=["Height", "Velocity"])
            utils.plot(time, self.follow.controller.get_fwd_output_detailed(), 
                block=False, title="Fwd output", ylabel="Velocity", legend=["p", "i", "d"])

        # Reset
        self.follow.is_follow_on = False
        await self.follow.pilot.set_position_ned_yaw(self.START_POS)
        update = input("Which to update? Y/F: ")
        p = float(input("Enter Kp: "))
        i = float(input("Enter Ki: "))
        d = float(input("Enter Kd: "))
        if update.lower() == 'y':
            self.follow.controller.yaw_pid.tunings = (p, i, d)
        elif update.lower() == 'f':
            self.follow.controller.fwd_pid.tunings = (p, i, d)
        self.follow.controller.reset()
        
        self.follow.is_follow_on = True


    async def keyboard_control(self, key):
        key_action = self.input_handler.handle(key)
        if key_action:
            if System.__name__ in key_action.__qualname__:
                try:
                    await key_action(self.follow.pilot)
                except ActionError as e:
                    self.log.error(e)
                    await self.follow.pilot.hold()     
            elif SolutionBase.__name__ in key_action.__qualname__:
                key_action(self.follow.pose, self.follow.source.get_blank())
        else:
            time_data = self.follow.controller.get_time_data()
            if (self.follow.is_follow_on and len(time_data) > 2 
                and int(time_data[-1] - time_data[0]) % 5 == 0
                and int(time_data[-2] - time_data[0]) % 5 != 0):
                self.log.warning(f"Elapsed {time_data[-1] - time_data[0]} seconds")


    async def restart_control(self, delay):
        await asyncio.sleep(delay)
        if not self.tune_yaw:
            await self.follow.pilot.set_velocity()
            await asyncio.sleep(delay * 2)

        self.follow.controller.reset()
        self.follow.is_follow_on = True


    def save_parameter_values(self, values):
        return_values = []
        found_variant = False
        for params in values:
            if found_variant or len(params) == 1:
                return_values.append([params[0]])
            elif len(params) > 1:
                found_variant = True
                self.legend = [f'{n:g}' for n in params]
                self.target_values = params
                return_values.append(params)
            else:
                raise Exception("Provide at least one value for each parameter")
        self.kp_values = return_values[0]
        self.ki_values = return_values[1]
        self.kd_values = return_values[2]


    def set_next_value(self):
        pid = self.follow.controller.yaw_pid if self.tune_yaw else self.follow.controller.fwd_pid
        if len(self.target_values) > 0:
            Kp, Ki, Kd = pid.tunings
            pid.tunings = (
                self.kp_values.pop(0) if len(self.kp_values) > 0 else Kp,
                self.kd_values.pop(0) if len(self.kd_values) > 0 else Kd,
                self.ki_values.pop(0) if len(self.ki_values) > 0 else Ki
            )
            self.log.info(f"Values left {len(self.target_values)}/{len(self.legend)}")
        else:
            self.plot_results(pid)
            raise KeyboardInterrupt
        

    def plot_results(self, pid):
        input_norm = [[pid.setpoint - i for i in sample] for sample in self.input_data]
        utils.plot(self.time, input_norm, legend=self.legend[3:], block=False,
                   title=("Yaw" if self.tune_yaw else "Forward") + " controller input",
                   ylabel="Computed error [-]")
        utils.plot(self.time, self.output_data, legend=self.legend[3:], block=False,
                   title=("Yaw" if self.tune_yaw else "Forward") + " controller output", 
                   ylabel="Output velocity" + " [deg/s]" if self.tune_yaw else " [m/s]")

        if self.tune_yaw:
            target_heading = math.atan(100 / 420) * 180 / math.pi + self.START_POS.yaw_deg
            pilot_time = [[i - sample[0] for i in sample] for sample in self.pilot_time]
            pilot_pos = [[i.yaw_deg for i in sample] for sample in self.pilot_pos_data]
            calculated_limits = [sum(sample[-(len(sample)//4):])/(len(sample)//4) for sample in pilot_pos]
            self.log.warn(f"Limits: {calculated_limits}")
            utils.plot(pilot_time + [[0, self.sample_time]], 
                       pilot_pos + [[target_heading, target_heading]],
                       block=False, title="Measured yaw position", legend=self.legend + ["Target"],
                       ylabel="Heading [deg]")
            utils.plot(pilot_time, [[i[1] for i in sample] for sample in self.pilot_vel_data], block=True,
                       title="Measured yaw speed" if self.tune_yaw else "Measured ground speed", legend=self.legend,
                       ylabel="Velocity [deg/s]")
        else:
            target_distance = -100 / 100
            pilot_time = [[i - sample[0] for i in sample] for sample in self.pilot_time]
            pilot_pos = [[i.north_m for i in sample] for sample in self.pilot_pos_data]
            calculated_limits = [sum(sample[-(len(sample)//4):])/(len(sample)//4) for sample in pilot_pos]
            self.log.warn(f"Limits: {calculated_limits}")
            utils.plot(pilot_time[3:] + [[0, self.sample_time]], 
                       pilot_pos[3:] + [[target_distance, target_distance]],
                       block=False, title="Measured forward position", legend=self.legend[3:] + ["Target"],
                       ylabel="Forward movement [m]")
            utils.plot(pilot_time[3:], [[i[0] for i in sample] for sample in self.pilot_vel_data][3:], block=True,
                       title="Measured yaw speed" if self.tune_yaw else "Measured ground speed", legend=self.legend[3:],
                       ylabel="Velocity" + " [rad/s]" if self.tune_yaw else " [m/s]")


    def close(self):
        self.log.info("All tasks finished")
        self.follow.close()