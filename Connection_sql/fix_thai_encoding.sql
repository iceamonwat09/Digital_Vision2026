/* ================================================================
   fix_thai_encoding.sql — ซ่อมข้อความภาษาไทยที่เพี้ยน (mojibake)

   ใช้เมื่อรัน auth_schema.sql ด้วย sqlcmd โดยไม่ได้ระบุ codepage UTF-8
   (-f 65001) ทำให้ description ภาษาไทยถูกเก็บผิด encoding เป็น "à¸..."

   *** ต้องรันด้วย -f 65001 ***  มิฉะนั้นจะเพี้ยนซ้ำอีก:
     sqlcmd -f 65001 -S 172.32.0.50 -d VisionIQ -U sa -P <pwd> -i Connection_sql\fix_thai_encoding.sql

   ปลอดภัยต่อการรันซ้ำ (เป็น UPDATE ตาม key/name ที่ตายตัว)
   ================================================================ */

SET NOCOUNT ON;

/* Role descriptions (แก้ได้เองภายหลังผ่านหน้า /admin/users) */
UPDATE dbo.AuthRoles SET Description = N'ผู้ดูแลระบบ — ทุกสิทธิ์'                 WHERE RoleName = 'Admin';
UPDATE dbo.AuthRoles SET Description = N'หัวหน้างาน — ตรวจได้ทุกโหมด + ดูประวัติ'   WHERE RoleName = 'Manager';
UPDATE dbo.AuthRoles SET Description = N'พนักงานตรวจ — ตรวจได้ทุกโหมด'           WHERE RoleName = 'Staff';
UPDATE dbo.AuthRoles SET Description = N'ผู้ดูอย่างเดียว — แดชบอร์ด + ประวัติ'       WHERE RoleName = 'Viewer';

/* Permission descriptions (ป้ายใน UI ใช้จาก config.py แล้ว แต่ซ่อมไว้ให้ตรงกัน) */
UPDATE dbo.AuthPermissions SET Description = N'ดูแดชบอร์ด'                WHERE PermissionKey = 'view_dashboard';
UPDATE dbo.AuthPermissions SET Description = N'ตรวจจับสด + ถ่ายรูปตรวจ'      WHERE PermissionKey = 'run_live_detection';
UPDATE dbo.AuthPermissions SET Description = N'ตรวจฉลากกระดาษ (ΔE2000)'    WHERE PermissionKey = 'inspect_label_paper';
UPDATE dbo.AuthPermissions SET Description = N'ตรวจ Artwork (OCR + 4 ชั้น)'   WHERE PermissionKey = 'inspect_artwork';
UPDATE dbo.AuthPermissions SET Description = N'ดูประวัติการตรวจ'            WHERE PermissionKey = 'view_history';
UPDATE dbo.AuthPermissions SET Description = N'จัดการผู้ใช้และสิทธิ์'          WHERE PermissionKey = 'manage_users';

PRINT 'Thai descriptions repaired. (ถ้ายังเพี้ยน แปลว่าลืมใส่ -f 65001)';
