"""
Create (or reset) an admin user — so nobody has to hand-write a bcrypt hash.

Run from the project root AFTER applying Connection_sql/auth_schema.sql:

    python -m auth.seed_admin --username admin --password 'Str0ng!Pass'

If --password is omitted you'll be prompted (input hidden). The password must
satisfy the policy in auth/config.py. Safe to re-run: it updates the existing
user's password/role instead of failing on a duplicate.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import config as ac, passwords, store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed/reset an admin user.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None,
                        help="omit to be prompted (hidden input)")
    parser.add_argument("--role", default="Admin",
                        choices=list(ac.ROLES.keys()))
    args = parser.parse_args(argv)

    if not passwords.hashing_available():
        print("✗ bcrypt ไม่ได้ติดตั้ง — `pip install bcrypt`")
        return 2
    if not store.db_available():
        print("✗ pyodbc ไม่ได้ติดตั้ง — `pip install pyodbc`")
        return 2

    password = args.password or getpass.getpass("New password: ")
    ok, errs = passwords.validate_password(password)
    if not ok:
        print("✗ รหัสผ่านไม่ผ่านเงื่อนไข:")
        for e in errs:
            print("   -", e)
        return 1

    role_id = store.get_role_id(args.role)
    if role_id is None:
        print(f"✗ ไม่พบ role '{args.role}' ในฐานข้อมูล — "
              "รัน Connection_sql/auth_schema.sql ก่อน")
        return 1

    try:
        uid = store.upsert_user(args.username, args.email,
                                passwords.hash_password(password), role_id)
    except Exception as e:
        print(f"✗ สร้าง/อัปเดตผู้ใช้ไม่สำเร็จ: {e}")
        return 1

    print(f"✓ พร้อมใช้งาน: user '{args.username}' (role={args.role}, id={uid})")
    print("  ลองเข้าสู่ระบบที่ /login")
    return 0


if __name__ == "__main__":
    sys.exit(main())
