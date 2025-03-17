#!/bin/bash

# Start Xvfb
Xvfb :99 -screen 0 1280x800x24 &
sleep 5

# Start the emulator in headless mode
emulator -avd $EMULATOR_NAME -no-audio -no-boot-anim -no-window -gpu swiftshader_indirect &
emulator_pid=$!

# Give the emulator time to boot
sleep 20

# Start the FastAPI application
uvicorn main:app --host 0.0.0.0 --port 8039

# Kill the emulator when the app exits
kill $emulator_pid