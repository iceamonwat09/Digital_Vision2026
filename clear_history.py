"""
Script to clear all defect history from VisionIQ database.
Run this script to delete all defect records from MongoDB.
"""

from database import Database

def main():
    print("=" * 60)
    print("VisionIQ - Clear Defect History")
    print("=" * 60)
    
    db = Database()
    if not db.connect():
        print("\nERROR: Could not connect to database.")
        print("Please ensure MongoDB is running.")
        return
    
    try:
        # Get current count
        count = db.collection.count_documents({})
        
        if count == 0:
            print("\nDatabase is already empty. No defects to delete.")
            db.disconnect()
            return
        
        print(f"\nFound {count} defect record(s) in the database.")
        print("\n⚠️  WARNING: This action cannot be undone!")
        print("All defect images and data will be permanently deleted.")
        
        response = input(f"\nAre you sure you want to delete all {count} records? (yes/no): ")
        
        if response.lower() in ['yes', 'y']:
            print("\nDeleting all defect records...")
            result = db.clear_all_defects()
            
            if result:
                print("✓ Successfully cleared all defect history!")
                print(f"✓ Deleted {count} defect record(s)")
            else:
                print("✗ Error: Failed to clear defect history")
        else:
            print("\nOperation cancelled. No records were deleted.")
    
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
    
    finally:
        db.disconnect()
        print("\nDatabase connection closed.")

if __name__ == "__main__":
    main()
