import os
import datetime
import time
import cv2
import asyncio
from enum import Enum
import mediapipe as mp

from dronecontrol.common import utils, pilot, input
from dronecontrol.common.video_source import CameraSource, SimulatorSource, FileSource
from dronecontrol.follow.controller import Controller
from dronecontrol.hands.graphics import HandGui
from dronecontrol.follow.image_processing import detect


mp_pose = mp.solutions.pose


class CameraMode(Enum):
    PICTURE = 0
    VIDEO = 1

class ImageDetection(Enum):
    NONE = 0
    HAND = 1
    POSE = 2

class VideoCamera:

    def __init__(self, use_simulator, use_hardware, use_wsl, use_camera, 
                 image_detection, hardware_address=None, simulator_ip=None,
                 file=None):
        self.log = utils.make_stdout_logger(__name__)
        self.input_handler = input.InputHandler()
        self.pilot = None
        if use_simulator or use_hardware:
            port = 14550 if use_simulator else None
            self.pilot = pilot.System(ip=simulator_ip, port=port, use_serial=use_hardware, serial_address=hardware_address)

        if use_camera:
            self.source = CameraSource()
        elif file:
            self.source = FileSource(file)
        elif use_simulator:
            self.source = SimulatorSource(utils.get_wsl_host_ip() if use_wsl else simulator_ip if simulator_ip else "")
        else:
            self.source = CameraSource()
        self.img = self.source.get_blank()

        self.mode = CameraMode.PICTURE
        self.is_recording = False
        self.out = None
        self.last_run_time = time.time()

        self.hand_detection = HandGui(source = self.source) if image_detection == ImageDetection.HAND else None
        self.pose_detection = mp_pose.Pose(model_complexity=1, min_detection_confidence=0.5, min_tracking_confidence=0.5) if image_detection == ImageDetection.POSE else None
        

    async def run(self):
        if self.pilot and not self.pilot.is_ready:
            try:
                await self.pilot.connect()
            except asyncio.exceptions.TimeoutError:
                self.log.error("Connection time-out")
                return
        pilot_task = asyncio.create_task(self.pilot.start_queue()) if self.pilot else None
        
        while True:
            if self.hand_detection:
                self.hand_detection.capture()
                raw_img = self.hand_detection.img
                self.img = raw_img.copy()
                self.hand_detection.draw_hands()
            else:
                raw_img = self.source.get_frame()
                self.img = raw_img.copy()

                if self.pose_detection:
                    self.results = self.pose_detection.process(self.img)
                    p1, p2 = detect(self.results, raw_img)
                    input = Controller.get_input(p1, p2) if self.results.pose_landmarks else (0, 0)
                    utils.write_text_to_image(raw_img, f"Yaw input: {input[0]:.3f}, fwd input {input[1]:.3f}", 
                                              utils.ImageLocation.BOTTOM_LEFT_LINE_TWO)

            utils.write_text_to_image(raw_img, f"Mode {self.mode.name}: {'' if self.is_recording else 'not '} recording",
                                      utils.ImageLocation.TOP_LEFT)
            utils.write_text_to_image(raw_img, f"FPS: {1.0 / (time.time() - self.last_run_time):.3f}")
            self.last_run_time = time.time()
            cv2.imshow("Dronecontrol: test camera", raw_img)

            try:
                self.__handle_key_input()
            except KeyboardInterrupt:
                break

            if self.is_recording:
                self.out.write(self.img)
            
            if pilot_task and pilot_task.done():
                break
            
            await asyncio.sleep(1 / 10)

        if pilot_task:
            if not pilot_task.done():
                pilot_task.cancel()
            try:
                await pilot_task
            except Exception as e:
                self.log.error(e)

        
    def close(self):
        cv2.waitKey(1)
        if self.out:
            self.out.release()
        self.source.close()
        if self.pilot:
            self.pilot.close()

        if self.pose_detection:
            self.pose_detection.close()


    def change_mode(self):
        if self.mode == CameraMode.VIDEO and self.is_recording:
            self.trigger()

        self.mode = CameraMode((self.mode.value + 1) % len(CameraMode))

    
    def trigger(self):
        if self.mode == CameraMode.PICTURE:
            img = self.img.copy()
            detect(self.results, img)
            utils.write_image(img)
        elif self.mode == CameraMode.VIDEO:
            if self.is_recording:
                self.out.release()
                self.out = None
            else:
                self.out = utils.write_video(self.source.get_size())
            self.is_recording = not self.is_recording


    def __handle_key_input(self):
        key_action = self.input_handler.handle(cv2.waitKey(self.source.get_delay()))
        if key_action is None:
            pass
        elif self.pilot and pilot.System.__name__ in key_action.__qualname__:
            self.pilot.queue_action(key_action)
        elif VideoCamera.__name__ in key_action.__qualname__:
            key_action(self)

    
