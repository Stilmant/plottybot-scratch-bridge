#!/usr/bin/python3
import signal
import asyncio
import websockets
import socket
import json
import threading
from queue import Queue

# Configuration
software_version = "1.2"
command_server_address = "127.0.0.1"
command_server_port = 1337
websocket_port = 8766
command_queue = Queue()
canvas_max_x = 0
canvas_max_y = 0

# Global shutdown flag
shutdown_event = threading.Event()
pen_state = "up"  # Initial state is up

def convert_coordinates(x, y):
    # Adjust coordinates for the plotter's canvas while rotating and scaling properly
    # X on the plotter is 0 to plotter_canvas_width (always 100 in your case)
    # Y on the plotter is larger (up to 150 or 160), but we scale it to match the aspect ratio
    # between Scratch's coordinates and the plotter's canvas, centered with an offset.

    # Calculate the proportional height for the plotter's canvas based on Scratch's 480x360 aspect ratio
    plotter_canvas_height = canvas_max_x * 480 / 360  # The height the Y axis should occupy on the plotter
    plotter_canvas_width = canvas_max_x  # X width remains the same (always 100)

    # Calculate the offset to center the plotter's Y axis
    canvas_y_center_offset = (plotter_canvas_height - plotter_canvas_width) / 2

    # Convert and invert X and Y
    plotter_x = plotter_canvas_width - ((y + 180) * plotter_canvas_width / 360)  # Scale Y from Scratch to X on plotter
    plotter_y = canvas_y_center_offset + (x + 240) * plotter_canvas_height / 480  # Scale X from Scratch to Y on plotter with centering offset

    return plotter_x, plotter_y

def send_command_to_hardware(command):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((command_server_address, command_server_port))
            s.sendall(command.encode('utf-8'))
            response = s.recv(1024)
        return response.decode('utf-8')
    except socket.error as e:
        print(f"Socket error: {e}")
        return "error"

# Process commands in the queue
async def command_consumer(command_queue):
    global canvas_max_x, canvas_max_y, pen_state
    calibrated = False
    while True:
        # Check if calibrated
        if not calibrated:
            print("Checking if plottybot is calibrated")
            # Get hardware status
            status = json.loads(send_command_to_hardware("get_status"))
            # out put log for debuggin: output only calibration_done, canvas_max_x, canvas_max_y
            print("Hardware status: ", status["calibration_done"], status["canvas_max_x"], status["canvas_max_y"])
            # If not calibrated, keep checking
            if not status["calibration_done"]:
                await asyncio.sleep(5)  # Check every 5 seconds
                continue
            else:
                canvas_max_x = status["canvas_max_x"]
                canvas_max_y = status["canvas_max_y"]
                print("Hardware calibrated and ready to plot commands on canvas size: ({}, {})".format(canvas_max_x, canvas_max_y))
                calibrated = True

        # Process commands if calibrated
        while calibrated:
            command = command_queue.get()
            print("Sending command to hardware: {}".format(command))
            response = send_command_to_hardware(command)
            if response != "ok":
                print("Error sending command to hardware: {}".format(response))
                calibrated = False
                break

            # Manage pen state based on the command
            if command == "pen_up":
                pen_state = "up"
            elif command == "pen_down":
                pen_state = "down"

# Websocket Server Logic
async def websocket_server(websocket, path):
    global pen_state, pen_up_task
    oldX = 0
    oldY = 0
    print("New Scratch client connected.")
    try:
        async for message in websocket:
            data = json.loads(message)

            if data["type"] == "goToXY":
                print(f"Received trace command: from ({data['oldX']}, {data['oldY']}) to ({data['x']}, {data['y']}) while at ({oldX}, {oldY})")
                if data["oldX"] != oldX or data["oldY"] != oldY:
                    # If oldX or oldY has changed, send a penUp command and move to the new 'old' location
                    print(f"Moving to location: ({data['oldX']}, {data['oldY']})")
                    if pen_state != "up":
                        command_queue.put("pen_up")
                        pen_state = "up"
                    x, y = convert_coordinates(data["oldX"], data["oldY"])
                    command_queue.put(f"go_to({x},{y})")

                print(f"Tracing to location: ({data['x']}, {data['y']})")
                if pen_state != "down":
                    command_queue.put("pen_down")
                    pen_state = "down"
                x, y = convert_coordinates(data["x"], data["y"])
                command_queue.put(f"go_to({x},{y})")
                oldX = data['x']
                oldY = data['y']

            if data["type"] == "penUp":
                print("Received penUp command")
                if pen_state != "up":
                    command_queue.put("pen_up")
                    pen_state = "up"

            if data["type"] == "penDown":
                print("Received penDown command")

            if data["type"] == "stop":
                # we call a function that will
                # stop sending commands
                # and clear the queue
                print("Received stop command")
                while not command_queue.empty():
                    command_queue.get()
                print("Queue cleared.")

            if data["type"] != "goToXY" and data["type"] != "penUp" and data["type"] != "penDown":
                print(f"Unknown command type: {data['type']}")

            await websocket.send("ok")
    except websockets.exceptions.ConnectionClosed:
        # When Scratch client disconnects
        #while not command_queue.empty():
        #    command_queue.get()
        print("Scratch client disconnected. Queue not cleared.")

async def start_websocket_server():
    async with websockets.serve(websocket_server, '0.0.0.0', websocket_port):
        print("WebSocket server started on port 8766")
        await asyncio.Future()  # run forever

def run_websocket_server():
    asyncio.run(start_websocket_server())

def run_command_consumer():
    asyncio.run(command_consumer(command_queue))

# Main function to start servers
def main():
    print("Starting PlottyBot-Scratch bridge...")
    print("Code Club 2024 - Version {}".format(software_version))
    print("Press Ctrl+C to stop the servers")
    print("Connecting to command server on port {}".format(command_server_port))

    # Create threads for websocket_server and command_consumer
    websocket_thread = threading.Thread(target=run_websocket_server, daemon=True)
    command_consumer_thread = threading.Thread(target=run_command_consumer, daemon=True)

    # Start threads
    websocket_thread.start()
    command_consumer_thread.start()

    # Main thread waits for "quit" command
    while True:
        user_input = input("Type 'quit' to stop the servers: ")
        if user_input == "quit":
            print("Shutting down...")
            shutdown_event.set()  # Signal all threads to shut down
            websocket_thread.join() # Wait for the threads to finish
            command_consumer_thread.join() # Wait for the threads to finish
            break

def shutdown_handler(signum, frame):
    print("Shutdown signal received. Cleaning up...")
    shutdown_event.set() # Signal the threads to close


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler) # For Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler) # For system kill command
    main()