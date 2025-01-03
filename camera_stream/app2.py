from flask import Flask, render_template, Response, request, redirect, url_for, session
from pypylon import pylon
import cv2
from flask_session import Session
import numpy as np
import time
import yaml
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Generate a random secret key for session management
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Load configuration from config.yaml
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
USERNAME = config['username']
PASSWORD = config['password']

# Initialize the Basler camera
camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
camera.Open()
camera.TriggerMode.SetValue('Off')  # Ensure the trigger mode is off for continuous acquisition
camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
converter = pylon.ImageFormatConverter()
converter.OutputPixelFormat = pylon.PixelType_BGR8packed
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

def adjust_exposure(exposure):
    """Adjust exposure to the nearest higher valid value."""
    exposure2 = exposure + 0.019
    return exposure2

def gen_frames():
    while camera.IsGrabbing():
        try:
            if camera.TriggerMode.GetValue() == 'Off':
                time.sleep(0.1)  # Limit to 10 frames per second (1/10 seconds per frame)
            grabResult = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                image = converter.Convert(grabResult)
                img = image.GetArray()
                ret, buffer = cv2.imencode('.jpg', img)
                frame = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            grabResult.Release()
        except pylon.TimeoutException as e:
            print(f"TimeoutException: {e}")
            continue

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
    if 'logged_in' in session:
        gain = request.form.get('gain', type=float)
        exposure = request.form.get('exposure', type=float)
        triggered = request.form.get('triggered', type=bool)

        if gain is not None:
            camera.Gain.SetValue(gain)
        if exposure is not None:
            exposure = adjust_exposure(exposure)
            camera.ExposureTime.SetValue(exposure * 1000.0)  # Convert milliseconds to microseconds

        if triggered:
            camera.TriggerMode.SetValue('On')
        else:
            camera.TriggerMode.SetValue('Off')

        return ('', 204)
    else:
        return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
