"""
================================================================
database.py  —  SQL Server 2014 version
แทนที่ MongoDB เดิมของ VisionIQ
Thai Union Group — Can Dent Detection
================================================================
วิธีใช้:
  1. วางไฟล์นี้แทน database.py เดิมใน VisionIQ folder
  2. เพิ่ม config ใน config.py (ดูด้านล่าง)
  3. pip install pyodbc

เพิ่มใน config.py:
    SQL_SERVER   = "YOUR_SERVER_NAME"
    SQL_DATABASE = "VisionIQ"
    PLANT_CODE   = "TUM1"
    LINE_NUMBER  = "LINE-01"
    DEFECT_CLASS_NAMES = {
        "can_dent": "Can Dent",
        "can_good": "Can Good",
    }
================================================================
"""

import pyodbc
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import base64
import cv2
import numpy as np
import config
from logger import setup_logger

logger = setup_logger(__name__)


class Database:
    """
    SQL Server 2014 handler
    Drop-in replacement สำหรับ MongoDB version
    ทุก Method ชื่อเหมือนเดิม — app.py ไม่ต้องแก้
    """

    def __init__(self):
        self.conn: Optional[pyodbc.Connection] = None
        self.is_connected = False

    # ----------------------------------------------------------
    # Connection
    # ----------------------------------------------------------
    def connect(self) -> bool:
        """เชื่อมต่อ SQL Server — SQL Server Authentication (UID/PWD)"""
        try:
            server   = getattr(config, "SQL_SERVER",   "localhost")
            database = getattr(config, "SQL_DATABASE", "VisionIQ")
            user     = getattr(config, "SQL_USER",     "sa")
            password = getattr(config, "SQL_PASSWORD", "")

            conn_str = (
                f"DRIVER={{SQL Server}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={user};"
                f"PWD={password};"
            )
            self.conn = pyodbc.connect(conn_str, timeout=5)
            self.conn.autocommit = False
            self.is_connected = True
            logger.info(f"SQL Server connected: {server}/{database} (user={user})")
            return True

        except Exception as e:
            logger.error(f"SQL Server connection failed: {e}")
            logger.error(
                "ตรวจสอบ: "
                "1) SQL Server เปิดอยู่และ TCP/IP enabled? "
                "2) SQL_SERVER / SQL_USER / SQL_PASSWORD ใน config.py ถูกต้อง? "
                "3) SQL Server Authentication mode เปิดอยู่?"
            )
            self.is_connected = False
            return False

    def disconnect(self):
        """ปิด Connection"""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.is_connected = False
            logger.info("SQL Server connection closed")

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------
    def encode_image(self, frame: np.ndarray, fmt: str = ".jpg") -> str:
        """แปลง Frame เป็น Base64 String"""
        try:
            if fmt == ".jpg":
                _, buf = cv2.imencode(".jpg", frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, 85])
            else:
                _, buf = cv2.imencode(".png", frame)
            return base64.b64encode(buf).decode("utf-8")
        except Exception as e:
            logger.error(f"Image encode error: {e}")
            return ""

    def _cursor(self):
        return self.conn.cursor()

    def _rows_to_dict(self, cursor) -> List[Dict]:
        """แปลง pyodbc rows → list of dict"""
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    # ----------------------------------------------------------
    # log_defect  ← เรียกจาก yolo_detector.py
    # ----------------------------------------------------------
    def log_defect(
        self,
        defect_type: str,
        confidence: float,
        frame: np.ndarray,
        bbox: List[int],
        timestamp: datetime = None
    ) -> Optional[str]:
        """
        บันทึก Defect ลง SQL Server
        รองรับ Interface เดิมของ VisionIQ ทุกอย่าง
        """
        if not self.is_connected:
            logger.warning("DB not connected — defect not logged")
            return None

        try:
            image_b64 = self.encode_image(frame)

            # แยก bbox coordinates
            if bbox and len(bbox) == 4:
                x1, y1, x2, y2 = [int(v) for v in bbox]
            else:
                x1 = y1 = x2 = y2 = None

            plant_code  = getattr(config, "PLANT_CODE",   "TUM1")
            line_number = getattr(config, "LINE_NUMBER",  None)
            cam_idx     = getattr(config, "CAMERA_INDEX", None)

            cur = self._cursor()
            cur.execute(
                "EXEC sp_log_defect ?,?,?,?,?,?,?,?,?,?",
                defect_type,
                float(confidence),
                image_b64 if image_b64 else None,
                x1, y1, x2, y2,
                cam_idx,
                plant_code,
                line_number
            )
            row    = cur.fetchone()
            new_id = str(int(row[0])) if row else None
            self.conn.commit()

            logger.debug(f"Defect logged id={new_id} type={defect_type} conf={confidence:.2f}")
            return new_id

        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            logger.error(f"log_defect failed: {e}")
            return None

    # ----------------------------------------------------------
    # get_all_defects  ← เรียกจาก app.py  GET /api/defects
    # ----------------------------------------------------------
    def get_all_defects(
        self,
        limit: int = 100,
        skip: int = 0,
        defect_type: str = None
    ) -> List[Dict]:
        """ดึง Defect History สำหรับ History Page"""
        if not self.is_connected:
            return []

        try:
            cur = self._cursor()
            cur.execute(
                "EXEC sp_get_defects ?,?,?",
                int(limit),
                int(skip),
                defect_type if defect_type else None
            )
            rows = self._rows_to_dict(cur)

            # แปลงให้ JSON Serializable
            for r in rows:
                if isinstance(r.get("timestamp"), datetime):
                    r["timestamp"] = r["timestamp"].isoformat()
                # ใช้ _id เหมือน MongoDB เดิม
                r["_id"] = str(r.get("id", ""))

            return rows

        except Exception as e:
            logger.error(f"get_all_defects failed: {e}")
            return []

    # ----------------------------------------------------------
    # get_statistics  ← เรียกจาก app.py  GET /api/stats
    # ----------------------------------------------------------
    def get_statistics(self) -> Dict:
        """ดึงสถิติสำหรับ Dashboard"""
        empty = {
            "total_defects":   0,
            "total_bottles":   0,
            "defects_by_type": {},
            "recent_defects":  0
        }

        if not self.is_connected:
            return empty

        try:
            plant_code = getattr(config, "PLANT_CODE", None)

            cur = self._cursor()
            cur.execute("EXEC sp_get_statistics ?,?", plant_code, 24)

            # ResultSet 1 — สถิติรวม
            r1     = cur.fetchone()
            total  = int(r1[0]) if r1 and r1[0] else 0

            # ResultSet 2 — ล่าสุด 24 ชม.
            cur.nextset()
            r2     = cur.fetchone()
            recent = int(r2[0]) if r2 and r2[0] else 0

            # ResultSet 3 — แบ่งตาม Type
            cur.nextset()
            by_type = {}
            class_names = getattr(config, "DEFECT_CLASS_NAMES", {})
            for row in cur.fetchall():
                key   = row[0]
                label = class_names.get(key, key.replace("_", " ").title())
                by_type[label] = int(row[1])

            return {
                "total_defects":   total,
                "total_bottles":   total,
                "defects_by_type": by_type,
                "recent_defects":  recent
            }

        except Exception as e:
            logger.error(f"get_statistics failed: {e}")
            return empty

    # ----------------------------------------------------------
    # get_time_series_data  ← Chart.js Dashboard
    # ----------------------------------------------------------
    def get_time_series_data(self, hours: int = 24) -> List[Dict]:
        """ดึงข้อมูล Time Series สำหรับ Graph"""
        if not self.is_connected:
            return []

        try:
            cur = self._cursor()
            cur.execute("EXEC sp_get_time_series ?", int(hours))
            return [
                {"timestamp": str(row[0]), "count": int(row[1])}
                for row in cur.fetchall()
            ]

        except Exception as e:
            logger.error(f"get_time_series_data failed: {e}")
            return []

    # ----------------------------------------------------------
    # clear_all_defects  ← clear_history.py
    # ----------------------------------------------------------
    def clear_all_defects(self) -> bool:
        """ลบข้อมูล Defect ทั้งหมด"""
        if not self.is_connected:
            return False

        try:
            cur = self._cursor()
            cur.execute("EXEC sp_clear_defects")
            row     = cur.fetchone()
            deleted = int(row[0]) if row else 0
            self.conn.commit()
            logger.info(f"Cleared {deleted} defect records")
            return True

        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            logger.error(f"clear_all_defects failed: {e}")
            return False


# ================================================================
# ทดสอบ Connection โดยตรง
# ================================================================
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    print("=" * 50)
    print("  VisionIQ Database Connection Test")
    print("=" * 50)

    db = Database()

    if db.connect():
        print("✅ Connection OK")

        stats = db.get_statistics()
        print(f"   Total defects : {stats['total_defects']}")
        print(f"   Recent 24h    : {stats['recent_defects']}")
        print(f"   By type       : {stats['defects_by_type']}")

        ts = db.get_time_series_data(hours=24)
        print(f"   Time series   : {len(ts)} data points")

        db.disconnect()
        print("✅ Test complete")
    else:
        print("❌ Connection failed")
        print("   แก้ SQL_SERVER ใน config.py แล้วลองใหม่")
