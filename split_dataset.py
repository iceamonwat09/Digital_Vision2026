import os
import random
import shutil

# ======================
# CONFIGURATION
# ======================
DATASET_DIR = "bottle_defect_dataset"

IMAGES_SRC = os.path.join(DATASET_DIR, "images")
LABELS_SRC = os.path.join(DATASET_DIR, "labels")

TRAIN_RATIO = 0.7
VAL_RATIO = 0.2
TEST_RATIO = 0.1

RANDOM_SEED = 42
# ======================

random.seed(RANDOM_SEED)

# Collect image files
image_files = [
    f for f in os.listdir(IMAGES_SRC)
    if f.lower().endswith((".jpg", ".jpeg", ".png"))
]

random.shuffle(image_files)

total = len(image_files)
train_end = int(total * TRAIN_RATIO)
val_end = train_end + int(total * VAL_RATIO)

train_files = image_files[:train_end]
val_files = image_files[train_end:val_end]
test_files = image_files[val_end:]

# Create destination folders
for split in ["train", "val", "test"]:
    os.makedirs(os.path.join(DATASET_DIR, "images", split), exist_ok=True)
    os.makedirs(os.path.join(DATASET_DIR, "labels", split), exist_ok=True)

def copy_image_and_label(files, split):
    for img in files:
        label = os.path.splitext(img)[0] + ".txt"

        src_img = os.path.join(IMAGES_SRC, img)
        src_label = os.path.join(LABELS_SRC, label)

        dst_img = os.path.join(DATASET_DIR, "images", split, img)
        dst_label = os.path.join(DATASET_DIR, "labels", split, label)

        shutil.copy2(src_img, dst_img)

        if os.path.exists(src_label):
            shutil.copy2(src_label, dst_label)
        else:
            print(f"⚠️ Missing label for {img}")

# Perform split
copy_image_and_label(train_files, "train")
copy_image_and_label(val_files, "val")
copy_image_and_label(test_files, "test")

print("✅ Dataset split completed successfully")
print(f"Train images: {len(train_files)}")
print(f"Validation images: {len(val_files)}")
print(f"Test images: {len(test_files)}")
