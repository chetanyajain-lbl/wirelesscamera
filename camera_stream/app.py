from flask import Flask, render_template, Response, request, redirect, url_for, session, jsonify
from pypylon import pylon
import cv2
from flask_session import Session
from flask_socketio import SocketIO, emit
import numpy as np
import time
import yaml
import os
import threading

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Generate a random secret key for session management
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)
socketio = SocketIO(app)

# Load configuration from config.yaml
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
USERNAME = config['username']
PASSWORD = config['password']

# Initialize the Basler camera
camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
camera.Open()
camera.PixelFormat.SetValue('Mono8')  # Set camera to 8-bit mode
camera.TriggerMode.SetValue('Off')  # Ensure the trigger mode is off for continuous acquisition
camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
converter = pylon.ImageFormatConverter()
converter.OutputPixelFormat = pylon.PixelType_Mono8
converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

# Set initial values for exposure and gain
initial_exposure = 20.0  # in milliseconds
initial_gain = 0.0

# Apply initial settings to the camera
camera.ExposureTime.SetValue(initial_exposure * 1000.0)  # Convert milliseconds to microseconds
camera.Gain.SetValue(initial_gain)

# Get the camera's exposure and gain limits (convert exposure to milliseconds)
exposure_min = camera.ExposureTime.GetMin() / 1000.0
exposure_max = camera.ExposureTime.GetMax() / 1000.0
gain_min = camera.Gain.GetMin()
gain_max = camera.Gain.GetMax()

# Initialize count_trigger and threshold
count_trigger = False
threshold = 0

# Initialize status variables
max_count = 0
mean_count = 0
streaming = False

# Lock for accessing the camera
camera_lock = threading.Lock()

def adjust_exposure(exposure):
    """Adjust exposure to the nearest higher valid value."""
    exposure2 = exposure + 0.019
    return exposure2

def get_current_settings():
    """Get the current exposure and gain settings."""
    current_exposure = camera.ExposureTime.GetValue() / 1000.0  # Convert microseconds to milliseconds
    current_gain = camera.Gain.GetValue()
    return current_exposure, current_gain

def gen_frames():
    global count_trigger, threshold, max_count, mean_count, streaming
    streaming = True
    while camera.IsGrabbing():
        with camera_lock:
            try:
                if camera.TriggerMode.GetValue() == 'Off':
                    time.sleep(0.1)  # Limit to 10 frames per second (1/10 seconds per frame)
                grabResult = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
                if grabResult.GrabSucceeded():
                    image = converter.Convert(grabResult)
                    img = image.GetArray()
                    
                    max_count = int(np.max(img))  # Convert to int
                    mean_count = round(float(np.mean(img)), 3)  # Convert to float
                    
                    if count_trigger and mean_count <= threshold:
                        # Create a white image
                        img = np.ones_like(img) * 255

                    ret, buffer = cv2.imencode('.jpg', img)
                    frame = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                grabResult.Release()
            except pylon.TimeoutException as e:
                print(f"TimeoutException: {e}")
                continue
    streaming = False

def update_camera_settings(gain, exposure, triggered):
    with camera_lock:
        if gain is not None:
            camera.Gain.SetValue(gain)
        if exposure is not None:
            exposure = adjust_exposure(exposure)
            camera.ExposureTime.SetValue(exposure * 1000.0)  # Convert milliseconds to microseconds
        if triggered is not None:
            if triggered:
                camera.TriggerMode.SetValue('On')
            else:
                camera.TriggerMode.SetValue('Off')

@app.route('/')
def index():
    if 'logged_in' in session:
        return render_template('index.html',
                               #exposure_min=exposure_min, exposure_max=exposure_max,
                               gain_min=gain_min, gain_max=gain_max,
                               initial_exposure=initial_exposure, initial_gain=initial_gain)
    else:
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == USERNAME and password == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return 'Invalid credentials, please try again.'
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/video_feed')
def video_feed():
    if 'logged_in' in session:
        return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
    else:
        return redirect(url_for('login'))

@app.route('/camera_control', methods=['POST'])
def camera_control():
    global count_trigger, threshold

    if 'logged_in' in session:
        gain = request.form.get('gain', type=float)
        exposure = request.form.get('exposure', type=float)
        triggered = request.form.get('triggered', type=bool)
        count_trigger = request.form.get('count_trigger') == 'on'
        threshold = request.form.get('threshold', type=int)

        if threshold is not None:
            threshold = max(0, min(threshold, 255))  # Ensure threshold is between 0 and 255

        # Update camera settings in a separate thread
        threading.Thread(target=update_camera_settings, args=(gain, exposure, triggered)).start()

        # Broadcast the new settings to all connected clients
        socketio.emit('update_settings', {'gain': gain, 'exposure': exposure, 'count_trigger': count_trigger, 'threshold': threshold})

        return ('', 204)
    else:
        return redirect(url_for('login'))

@app.route('/camera_status')
def camera_status():
    if 'logged_in' in session:
        if streaming:
            return jsonify(max_count=int(max_count), mean_count=float(mean_count))
        else:
            return jsonify(max_count="N/A", mean_count="N/A")
    else:
        return redirect(url_for('login'))

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
