"""
Training Script for Custom Bottle Defect Detection Model
Optimized for 250+ images with aggressive augmentation
"""

from ultralytics import YOLO
import os
from pathlib import Path

def main():
    print("=" * 70)
    print("VisionIQ - Custom Model Training")
    print("=" * 70)
    
    # Check dataset structure
    dataset_dir = Path("bottle_defect_dataset")
    data_yaml = dataset_dir / "data.yaml"
    
    if not data_yaml.exists():
        print(f"\n❌ ERROR: data.yaml not found at {data_yaml}")
        print("   Please ensure data.yaml exists in bottle_defect_dataset/")
        return
    
    # Check for images
    train_images = dataset_dir / "images" / "train"
    val_images = dataset_dir / "images" / "val"
    
    if not train_images.exists():
        print(f"\n❌ ERROR: Training images not found at {train_images}")
        return
    
    train_count = len(list(train_images.glob("*.jpg"))) + len(list(train_images.glob("*.png")))
    val_count = len(list(val_images.glob("*.jpg"))) + len(list(val_images.glob("*.png")))
    
    print(f"\n📊 Dataset Analysis:")
    print(f"   Training images: {train_count}")
    print(f"   Validation images: {val_count}")
    print(f"   Total: {train_count + val_count} images")
    
    if train_count < 50:
        print("\n⚠️  WARNING: Low number of training images!")
        print("   Recommended: 100+ images per class")
        print("   Using aggressive augmentation...")
        epochs = 300
        batch = 4
    elif train_count < 200:
        print("\n📦 Moderate Dataset Size")
        print("   Using enhanced augmentation...")
        epochs = 250
        batch = 8
    else:
        print("\n✅ Good Dataset Size")
        epochs = 200
        batch = 16
    
    print(f"\n⚙️  Training Configuration:")
    print(f"   Model: yolov8n.pt (nano - fastest for real-time)")
    print(f"   Epochs: {epochs}")
    print(f"   Batch Size: {batch}")
    print(f"   Image Size: 640x640")
    print(f"   Augmentation: AGGRESSIVE")
    
    print("\n🚀 Starting Training...")
    print("   This may take 1-10 hours depending on your hardware")
    print("   Best model will be saved automatically")
    print("   Press Ctrl+C to stop (model will be saved at last checkpoint)\n")
    
    try:
        # Load pretrained model
        model = YOLO('yolov8n.pt')
        
        # Train with aggressive augmentation for 250+ images
        results = model.train(
            data=str(data_yaml),
            epochs=epochs,
            imgsz=640,
            batch=batch,
            name='bottle_defects',
            device=0,  # Use GPU if available, else 'cpu'
            patience=100,
            save=True,
            save_period=10,
            val=True,
            plots=True,
            verbose=True,
            
            # Learning rate
            lr0=0.01,
            lrf=0.1,
            momentum=0.937,
            weight_decay=0.0005,
            
            # Aggressive augmentation for limited data
            hsv_h=0.02,        # Hue variation
            hsv_s=0.8,         # Saturation variation (high!)
            hsv_v=0.5,         # Brightness variation
            degrees=15.0,      # Rotation up to 15 degrees
            translate=0.2,     # Translation 20%
            scale=0.9,         # Scale variation
            shear=5.0,         # Shear transformation
            perspective=0.0005, # Perspective transformation
            flipud=0.5,        # Vertical flip 50%
            fliplr=0.5,        # Horizontal flip 50%
            mosaic=1.0,         # Mosaic augmentation (combines 4 images)
            mixup=0.3,         # Mixup augmentation (blends 2 images)
            copy_paste=0.3,    # Copy-paste augmentation
        )
        
        print("\n" + "=" * 70)
        print("✅ Training Completed Successfully!")
        print("=" * 70)
        
        model_path = dataset_dir / "runs" / "detect" / "bottle_defects" / "weights" / "best.pt"
        last_path = dataset_dir / "runs" / "detect" / "bottle_defects" / "weights" / "last.pt"
        
        print(f"\n📁 Model Location:")
        print(f"   Best Model: {model_path}")
        print(f"   Last Checkpoint: {last_path}")
        
        print(f"\n📊 Results:")
        results_dir = dataset_dir / "runs" / "detect" / "bottle_defects"
        print(f"   Check: {results_dir / 'results.png'}")
        print(f"   Training plots: {results_dir / 'train_batch*.jpg'}")
        
        print(f"\n🔧 Next Steps:")
        print(f"   1. Update config.py MODEL_PATH to:")
        print(f"      MODEL_PATH = r\"{model_path}\"")
        print(f"   2. Restart the application: python app.py")
        print(f"   3. Test detection on Live Detection page")
        print("=" * 70)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Training interrupted by user")
        last_path = dataset_dir / "runs" / "detect" / "bottle_defects" / "weights" / "last.pt"
        print(f"   Last checkpoint saved at: {last_path}")
        print("   You can resume training or use the last checkpoint")
    except Exception as e:
        print(f"\n❌ Training Error: {str(e)}")
        print("\nTroubleshooting:")
        print("   1. Check data.yaml exists and is correctly formatted")
        print("   2. Verify images/ and labels/ folders exist")
        print("   3. Ensure class names match between data.yaml and config.py")
        print("   4. Check that all images have corresponding label files")

if __name__ == "__main__":
    main()
