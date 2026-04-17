"""
Quick test script to diagnose camera issues.
Run this to check if your external webcam is accessible.
"""

import cv2
import sys
import config

print("=" * 60)
print("Camera Diagnostic Test")
print("=" * 60)

# Test direct OpenCV access
print(f"\nTesting camera at index {config.CAMERA_INDEX}...")
cap = cv2.VideoCapture(config.CAMERA_INDEX)

if not cap.isOpened():
    print(f"ERROR: Cannot open camera at index {config.CAMERA_INDEX}")
    print("\nTrying to find available cameras...")
    
    available = []
    for i in range(5):
        test_cap = cv2.VideoCapture(i)
        if test_cap.isOpened():
            ret, frame = test_cap.read()
            if ret and frame is not None:
                available.append(i)
                print(f"  [OK] Camera found at index {i}")
            test_cap.release()
    
    if available:
        print(f"\nAvailable cameras: {available}")
        print(f"Please update CAMERA_INDEX in config.py to one of: {available}")
    else:
        print("\nERROR: No cameras found! Please check your camera connection.")
    sys.exit(1)

print("[OK] Camera opened successfully")

# Try to read a frame
ret, frame = cap.read()
if not ret or frame is None:
    print("ERROR: Cannot read frame from camera")
    print("Camera may be in use by another application")
    cap.release()
    sys.exit(1)

print(f"[OK] Frame read successfully: {frame.shape}")

# Try to set properties
try:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[OK] Resolution set: {actual_width}x{actual_height}")
except Exception as e:
    print(f"Warning: Could not set all properties: {e}")

cap.release()
print("\n" + "=" * 60)
print("[OK] Camera test passed! Camera is ready to use.")
print("=" * 60)
