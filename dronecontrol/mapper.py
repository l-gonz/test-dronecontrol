import asyncio

from dronecontrol import graphics, utils
from .gestures import Gesture
from .pilot import System

def map_gesture_to_action(system, gesture):
    """Map a hand gesture to a drone action."""
    
    if gesture == Gesture.NO_HAND:
        return system.queue_action(System.return_home, interrupt=True)
    if gesture == Gesture.STOP:
        return system.queue_action(System.land)
    if gesture == Gesture.FIST:
        return system.queue_action(System.takeoff)
    if gesture == Gesture.POINT_UP:
        return system.queue_action(System.start_offboard)
    if gesture == Gesture.POINT_RIGHT:
        return system.queue_action(System.set_velocity, params={"right": 1.0})
    if gesture == Gesture.POINT_LEFT:
        return system.queue_action(System.set_velocity, params={"right": -1.0})
    if gesture == Gesture.THUMB_RIGHT:
        return system.queue_action(System.set_velocity, params={"forward": 1.0})
    if gesture == Gesture.THUMB_LEFT:
        return system.queue_action(System.set_velocity, params={"forward": -1.0})
    


async def run_gui(gui):
    """Run loop for the interface to capture
    the image and render it.
    
    Return whether the loop should continue."""
    gui.capture()
    if gui.render() >= 0:
        return False
    await asyncio.sleep(0.03)
    return True


async def cancel_pending(task):
    """Stop previous running tasks."""
    if not task.done():
        task.cancel()
    await task
    log.info("All tasks finished")


async def run():
    """Runs the GUI loop and the drone control thread simultaneously."""
    global log
    log = utils.make_logger(__name__)
    system = System()
    gui = graphics.HandGui()
    
    task = asyncio.create_task(system.start())
    gui.subscribe_to_gesture(lambda g: map_gesture_to_action(system, g))

    log.info("Starting graphics")
    while True:
        if not await run_gui(gui):
            break
        if task.done():
            break

    log.warning("System stop")
    await cancel_pending(task)


if __name__ == "__main__":
    asyncio.run(run())
