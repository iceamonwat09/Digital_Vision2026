/* ================================================================
   auth_schema.sql  —  Authentication + RBAC tables for VisionIQ
   SQL Server 2014+  •  idempotent (safe to run more than once)

   Run against the VisionIQ database — IMPORTANT: ใส่ -f 65001 (UTF-8) เสมอ
   ไม่งั้นข้อความไทยจะเพี้ยนเป็น mojibake ตอน sqlcmd อ่านไฟล์:
     sqlcmd -f 65001 -S 172.32.0.50 -d VisionIQ -U sa -P <pwd> -i Connection_sql/auth_schema.sql

   Then create the first admin with:
     python -m auth.seed_admin --username admin --password '<StrongPass1!>'
   ================================================================ */

SET NOCOUNT ON;

/* ── Roles ──────────────────────────────────────────────────── */
IF OBJECT_ID('dbo.AuthRoles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthRoles (
        RoleId      INT IDENTITY(1,1) PRIMARY KEY,
        RoleName    NVARCHAR(50)  NOT NULL UNIQUE,
        Description NVARCHAR(200) NULL
    );
END;

/* ── Permissions ────────────────────────────────────────────── */
IF OBJECT_ID('dbo.AuthPermissions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthPermissions (
        PermissionId  INT IDENTITY(1,1) PRIMARY KEY,
        PermissionKey NVARCHAR(64)  NOT NULL UNIQUE,
        Description   NVARCHAR(200) NULL
    );
END;

/* ── Role ↔ Permission (many-to-many) ───────────────────────── */
IF OBJECT_ID('dbo.AuthRolePermissions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthRolePermissions (
        RoleId       INT NOT NULL,
        PermissionId INT NOT NULL,
        CONSTRAINT PK_AuthRolePermissions PRIMARY KEY (RoleId, PermissionId),
        CONSTRAINT FK_ARP_Role       FOREIGN KEY (RoleId)
            REFERENCES dbo.AuthRoles (RoleId) ON DELETE CASCADE,
        CONSTRAINT FK_ARP_Permission FOREIGN KEY (PermissionId)
            REFERENCES dbo.AuthPermissions (PermissionId) ON DELETE CASCADE
    );
END;

/* ── Users ──────────────────────────────────────────────────── */
IF OBJECT_ID('dbo.AuthUsers', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthUsers (
        UserId         INT IDENTITY(1,1) PRIMARY KEY,
        Username       NVARCHAR(64)  NOT NULL UNIQUE,
        Email          NVARCHAR(256) NULL,
        PasswordHash   NVARCHAR(255) NOT NULL,   -- bcrypt (never plain text)
        RoleId         INT           NOT NULL,
        IsActive       BIT           NOT NULL DEFAULT 1,
        FailedAttempts INT           NOT NULL DEFAULT 0,
        LockedUntil    DATETIME2     NULL,
        LastLoginAt    DATETIME2     NULL,
        CreatedAt      DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_AuthUsers_Role FOREIGN KEY (RoleId)
            REFERENCES dbo.AuthRoles (RoleId)
    );
END;

/* ── Login audit trail ──────────────────────────────────────── */
IF OBJECT_ID('dbo.AuthLoginAudit', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthLoginAudit (
        AuditId     BIGINT IDENTITY(1,1) PRIMARY KEY,
        Username    NVARCHAR(64)  NULL,
        UserId      INT           NULL,
        Success     BIT           NOT NULL,
        Ip          NVARCHAR(64)  NULL,
        UserAgent   NVARCHAR(400) NULL,
        Reason      NVARCHAR(200) NULL,
        AttemptedAt DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    );
    CREATE INDEX IX_AuthLoginAudit_AttemptedAt
        ON dbo.AuthLoginAudit (AttemptedAt DESC);
END;

/* ================================================================
   SEED — roles, permissions, role↔permission map
   (all guarded with NOT EXISTS so re-running changes nothing)
   ================================================================ */

/* Roles */
MERGE dbo.AuthRoles AS t
USING (VALUES
    ('Admin',   N'ผู้ดูแลระบบ — ทุกสิทธิ์'),
    ('Manager', N'หัวหน้างาน — ตรวจได้ทุกโหมด + ดูประวัติ'),
    ('Staff',   N'พนักงานตรวจ — ตรวจได้ทุกโหมด'),
    ('Viewer',  N'ผู้ดูอย่างเดียว — แดชบอร์ด + ประวัติ')
) AS s (RoleName, Description)
ON t.RoleName = s.RoleName
WHEN NOT MATCHED THEN
    INSERT (RoleName, Description) VALUES (s.RoleName, s.Description);

/* Permissions (keys MUST match auth/config.py PERMISSIONS) */
MERGE dbo.AuthPermissions AS t
USING (VALUES
    ('view_dashboard',      N'ดูแดชบอร์ด'),
    ('run_live_detection',  N'ตรวจจับสด + ถ่ายรูปตรวจ'),
    ('inspect_label_paper', N'ตรวจฉลากกระดาษ (ΔE2000)'),
    ('inspect_artwork',     N'ตรวจ Artwork (OCR + 4 ชั้น)'),
    ('view_history',        N'ดูประวัติการตรวจ'),
    ('manage_users',        N'จัดการผู้ใช้และสิทธิ์')
) AS s (PermissionKey, Description)
ON t.PermissionKey = s.PermissionKey
WHEN NOT MATCHED THEN
    INSERT (PermissionKey, Description) VALUES (s.PermissionKey, s.Description);

/* Role → Permission grants */
;WITH grants (RoleName, PermissionKey) AS (
    SELECT 'Admin', PermissionKey FROM dbo.AuthPermissions          -- all
    UNION ALL SELECT 'Manager', 'view_dashboard'
    UNION ALL SELECT 'Manager', 'run_live_detection'
    UNION ALL SELECT 'Manager', 'inspect_label_paper'
    UNION ALL SELECT 'Manager', 'inspect_artwork'
    UNION ALL SELECT 'Manager', 'view_history'
    UNION ALL SELECT 'Staff',   'view_dashboard'
    UNION ALL SELECT 'Staff',   'run_live_detection'
    UNION ALL SELECT 'Staff',   'inspect_label_paper'
    UNION ALL SELECT 'Staff',   'inspect_artwork'
    UNION ALL SELECT 'Viewer',  'view_dashboard'
    UNION ALL SELECT 'Viewer',  'view_history'
)
INSERT INTO dbo.AuthRolePermissions (RoleId, PermissionId)
SELECT r.RoleId, p.PermissionId
FROM grants g
JOIN dbo.AuthRoles r       ON r.RoleName      = g.RoleName
JOIN dbo.AuthPermissions p ON p.PermissionKey = g.PermissionKey
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.AuthRolePermissions rp
    WHERE rp.RoleId = r.RoleId AND rp.PermissionId = p.PermissionId
);

PRINT 'auth_schema.sql applied. Next: python -m auth.seed_admin --username admin --password <StrongPass1!>';
