"""กันการยิงคำขอรัว ๆ (ผู้ใช้สั่ง 4 ต.ค.: "ระบบต้องป้องกันการส่ง Request รัว")

ทุกการกด "ตรวจ" = เรียก Cloud Vision จริง (เสียโควตา/เงิน) ⇒ ฝั่งเซิร์ฟเวอร์ต้องกันเอง
ไม่พึ่งปุ่มที่ถูก disable ในเบราว์เซอร์อย่างเดียว (ดับเบิลคลิก · กด Enter · เปิดสองแท็บ ·
รีเฟรชระหว่างรอแล้วกดใหม่ · สคริปต์ยิงตรง)

สามด่าน — ทุกด่านตั้งเป็น 0 เพื่อปิดได้:

1. **งานเดียวกันวิ่งได้ทีละรอบ** — รอบที่สองระหว่างรอบแรกยังไม่จบ ⇒ ปฏิเสธ (409)
2. **ทั้งเครื่องวิ่งพร้อมกันได้ไม่เกิน ``max_concurrent`` รอบ** ⇒ ปฏิเสธ (429)
3. **พักหลังจบรอบ ``cooldown_s`` วินาที** ต่องาน ⇒ ปฏิเสธ (429) พร้อมบอกว่าต้องรออีกกี่วินาที
   (กันการกดซ้ำที่ค้างอยู่ในคิวของเบราว์เซอร์ทันทีหลังได้ผล)

ไม่เข้าคิว/ไม่รอ — คำขอที่ถูกปฏิเสธไม่ถูกส่งต่อไปที่ Vision เลย
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional


class Busy(Exception):
    """ถูกปฏิเสธ · ``status`` = HTTP status ที่ควรตอบ · ``retry_after`` = วินาทีที่ควรรอ"""

    def __init__(self, message: str, status: int, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RunGuard:
    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._running: Dict[str, float] = {}
        self._ended: Dict[str, float] = {}
        self._clock = clock

    def acquire(self, key: str, max_concurrent: int = 0, cooldown_s: float = 0.0) -> None:
        """จองสิทธิ์วิ่ง ``key`` (เช่นรหัสงาน) · ไม่ผ่าน ⇒ ``Busy``"""
        now = self._clock()
        with self._lock:
            if key in self._running:
                raise Busy("งานนี้กำลังตรวจอยู่ (เริ่มเมื่อ %.0f วินาทีก่อน) — รอผลรอบนี้ก่อน"
                           " ระบบไม่ส่งคำขอซ้ำ" % (now - self._running[key]), 409)
            if max_concurrent > 0 and len(self._running) >= max_concurrent:
                raise Busy("มีการตรวจกำลังทำงานอยู่ %d รอบ (เพดาน %d) — ลองใหม่อีกครู่"
                           % (len(self._running), max_concurrent), 429, 5.0)
            if cooldown_s > 0 and key in self._ended:
                wait = cooldown_s - (now - self._ended[key])
                if wait > 0:
                    raise Busy("เพิ่งตรวจงานนี้เสร็จ — รออีก %.1f วินาทีแล้วค่อยกดใหม่"
                               " (กันการกดซ้ำโดยไม่ตั้งใจ)" % wait, 429, wait)
            self._running[key] = now

    def release(self, key: str, cooldown: bool = True) -> None:
        """คืนสิทธิ์ · ``cooldown=False`` = รอบนี้ไม่ได้ยิงอะไรออกไป (เช่นข้อมูลไม่ครบ) ไม่ต้องพัก"""
        with self._lock:
            self._running.pop(key, None)
            if cooldown:
                self._ended[key] = self._clock()
                if len(self._ended) > 512:             # กันโตไม่หยุด — เก็บแค่ล่าสุด
                    for k, _ in sorted(self._ended.items(), key=lambda kv: kv[1])[:256]:
                        self._ended.pop(k, None)

    def running(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._running)

