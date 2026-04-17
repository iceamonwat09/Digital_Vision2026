"""
Centralized logging configuration for VisionIQ.
Creates logs directory and configures logging for the entire project.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
import config

# Create logs directory
LOGS_DIR = os.path.join(config.BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Log file paths
LOG_FILE = os.path.join(LOGS_DIR, "visioniq.log")
ERROR_LOG_FILE = os.path.join(LOGS_DIR, "errors.log")
DETECTION_LOG_FILE = os.path.join(LOGS_DIR, "detections.log")


def setup_logger(name: str = None, log_level: int = logging.INFO) -> logging.Logger:
    """
    Setup and return a logger instance.
    
    Args:
        name: Logger name (typically __name__)
        log_level: Logging level (default: INFO)
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name or "visioniq")
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    logger.setLevel(log_level)
    
    # Formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    simple_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler (INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)
    
    # File handler (all levels)
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    logger.addHandler(file_handler)
    
    # Error file handler (ERROR and above)
    error_handler = logging.FileHandler(ERROR_LOG_FILE, encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(detailed_formatter)
    logger.addHandler(error_handler)
    
    return logger


def get_detection_logger() -> logging.Logger:
    """
    Get logger specifically for detection events.
    
    Returns:
        Logger instance configured for detection logging
    """
    logger = logging.getLogger("visioniq.detection")
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Detection-specific file handler
        handler = logging.FileHandler(DETECTION_LOG_FILE, encoding='utf-8')
        handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        # Don't propagate to root logger to avoid duplicates
        logger.propagate = False
    
    return logger


# Initialize root logger
root_logger = setup_logger("visioniq")


def log_detection(defect_type: str, confidence: float, bbox: list):
    """
    Log a detection event to the detection log file.
    
    Args:
        defect_type: Type of defect detected
        confidence: Confidence score
        bbox: Bounding box coordinates
    """
    detection_logger = get_detection_logger()
    detection_logger.info(
        f"Defect: {defect_type} | Confidence: {confidence:.3f} | "
        f"BBox: [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]"
    )


def cleanup_old_logs(days_to_keep: int = 30):
    """
    Clean up log files older than specified days.
    
    Args:
        days_to_keep: Number of days to keep logs (default: 30)
    """
    import time
    current_time = time.time()
    cutoff_time = current_time - (days_to_keep * 24 * 60 * 60)
    
    for log_file in [LOG_FILE, ERROR_LOG_FILE, DETECTION_LOG_FILE]:
        if os.path.exists(log_file):
            try:
                file_time = os.path.getmtime(log_file)
                if file_time < cutoff_time:
                    os.remove(log_file)
                    root_logger.info(f"Cleaned up old log file: {log_file}")
            except Exception as e:
                root_logger.warning(f"Failed to clean up log file {log_file}: {str(e)}")


if __name__ == "__main__":
    # Test logging
    logger = setup_logger("test")
    logger.info("Logger test - INFO message")
    logger.warning("Logger test - WARNING message")
    logger.error("Logger test - ERROR message")
    
    detection_logger = get_detection_logger()
    detection_logger.info("Detection logger test")
    
    print(f"\nLogs directory: {LOGS_DIR}")
    print(f"Main log: {LOG_FILE}")
    print(f"Error log: {ERROR_LOG_FILE}")
    print(f"Detection log: {DETECTION_LOG_FILE}")
