"""
Few-Shot Learning Training Script for VisionIQ
Optimized for training with limited images (10-50 per class)
Uses aggressive data augmentation to maximize dataset size
"""

from ultralytics import YOLO
import os

def count_images(dataset_path='.'):
    """Count training images in dataset."""
    train_path = os.path.join(dataset_path, 'images', 'train')
    if os.path.exists(train_path):
        images = [f for f in os.listdir(train_path) if f.endswith(('.jpg', '.png', '.jpeg'))]
        return len(images)
    return 0

def main():
    print("=" * 70)
    print("VisionIQ - Few-Shot Learning Training")
    print("=" * 70)
    
    # Check dataset size
    dataset_size = count_images('.')
    print(f"\n📊 Dataset Analysis:")
    print(f"   Training images found: {dataset_size}")
    
    if dataset_size == 0:
        print("\n❌ ERROR: No training images found!")
        print("   Expected structure: images/train/")
        return
    
    # Determine training strategy based on dataset size
    if dataset_size < 100:
        print("⚠️  Few-Shot Learning Mode Detected!")
        print("   Using aggressive augmentation strategy...")
        
        epochs = 300
        batch = 4
        patience = 100
        
        # Aggressive augmentation for few-shot
        augmentation = {
            'hsv_h': 0.02,
            'hsv_s': 0.8,
            'hsv_v': 0.5,
            'degrees': 15.0,
            'translate': 0.2,
            'scale': 0.9,
            'shear': 5.0,
            'perspective': 0.0005,
            'flipud': 0.5,
            'fliplr': 0.5,
            'mosaic': 1.0,
            'mixup': 0.3,
            'copy_paste': 0.3,
        }
        
        print(f"\n⚙️  Training Configuration:")
        print(f"   Epochs: {epochs}")
        print(f"   Batch Size: {batch}")
        print(f"   Augmentation: AGGRESSIVE")
        
    elif dataset_size < 300:
        print("📦 Small Dataset Mode")
        print("   Using moderate augmentation...")
        
        epochs = 200
        batch = 8
        patience = 75
        
        augmentation = {
            'hsv_h': 0.015,
            'hsv_s': 0.7,
            'hsv_v': 0.4,
            'degrees': 10.0,
            'translate': 0.15,
            'scale': 0.7,
            'shear': 3.0,
            'perspective': 0.0003,
            'flipud': 0.3,
            'fliplr': 0.5,
            'mosaic': 1.0,
            'mixup': 0.2,
            'copy_paste': 0.2,
        }
        
        print(f"\n⚙️  Training Configuration:")
        print(f"   Epochs: {epochs}")
        print(f"   Batch Size: {batch}")
        print(f"   Augmentation: MODERATE")
        
    else:
        print("✅ Standard Dataset Size")
        print("   Using standard augmentation...")
        
        epochs = 100
        batch = 16
        patience = 50
        
        augmentation = {
            'hsv_h': 0.015,
            'hsv_s': 0.7,
            'hsv_v': 0.4,
            'degrees': 0.0,
            'translate': 0.1,
            'scale': 0.5,
            'mosaic': 1.0,
            'mixup': 0.0,
        }
        
        print(f"\n⚙️  Training Configuration:")
        print(f"   Epochs: {epochs}")
        print(f"   Batch Size: {batch}")
        print(f"   Augmentation: STANDARD")
    
    # Check if data.yaml exists
    if not os.path.exists('data.yaml'):
        print("\n❌ ERROR: data.yaml not found!")
        print("   Please create data.yaml file (see TRAINING_GUIDE.md)")
        return
    
    print("\n🚀 Starting Training...")
    print("   This may take 1-10 hours depending on dataset size and hardware")
    print("   Press Ctrl+C to stop (model will be saved at last checkpoint)\n")
    
    try:
        # Load pretrained model
        model = YOLO('yolov8n.pt')
        
        # Train with optimized settings
        results = model.train(
            data='data.yaml',
            epochs=epochs,
            imgsz=640,
            batch=batch,
            name='bottle_defects_fewshot',
            device=0,  # Use GPU if available, else 'cpu'
            patience=patience,
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
            # Augmentation
            **augmentation
        )
        
        print("\n" + "=" * 70)
        print("✅ Training Completed Successfully!")
        print("=" * 70)
        print(f"\n📁 Model Location:")
        print(f"   Best Model: runs/detect/bottle_defects_fewshot/weights/best.pt")
        print(f"   Last Checkpoint: runs/detect/bottle_defects_fewshot/weights/last.pt")
        print(f"\n📊 Results:")
        print(f"   Check: runs/detect/bottle_defects_fewshot/results.png")
        print(f"\n🔧 Next Steps:")
        print(f"   1. Copy best.pt to your project directory")
        print(f"   2. Update MODEL_PATH in config.py")
        print(f"   3. Test with: python app.py")
        print("=" * 70)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Training interrupted by user")
        print("   Last checkpoint saved at: runs/detect/bottle_defects_fewshot/weights/last.pt")
    except Exception as e:
        print(f"\n❌ Training Error: {str(e)}")
        print("   Please check your dataset structure and data.yaml file")

if __name__ == "__main__":
    main()
