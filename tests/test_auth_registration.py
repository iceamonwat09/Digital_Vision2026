"""ลงทะเบียนด้วยตนเองจากหน้า /login — ใครสร้างบัญชีได้ และได้สิทธิ์อะไร.

จุดที่ต้องกันให้แน่นคือ "endpoint สาธารณะที่เขียน DB ได้": ต้องรับเฉพาะอีเมล
โดเมนบริษัท, role ต้องถูก fix ที่ฝั่งเซิร์ฟเวอร์ (client ส่ง role มาต้องไม่มีผล),
และ username ต้องเท่ากับอีเมลเสมอ. เทสต์ mock ชั้น store/bcrypt ทั้งหมด จึงรันได้
โดยไม่ต้องมี SQL Server หรือ bcrypt บนเครื่อง.
"""

import os

import pytest
from flask import Flask

from auth import config as ac, passwords, registration, store
from auth.access import _is_public, _required_permission
from auth.routes import auth_bp

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── ชั้นกติกา (pure) ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("somchai.s@thaiunion.com", "somchai.s@thaiunion.com"),
    ("  Somchai.S@ThaiUnion.COM  ", "somchai.s@thaiunion.com"),   # ตัวใหญ่ได้
    ("A_B+tag@THAIUNION.com", "a_b+tag@thaiunion.com"),
])
def test_email_accepted_and_normalized(raw, expect):
    ok, email, err = registration.check_email(raw, domains=["thaiunion.com"])
    assert ok is True and err == ""
    assert email == expect


@pytest.mark.parametrize("raw", [
    "somchai@gmail.com",             # โดเมนอื่น
    "somchai@evil-thaiunion.com",    # โดเมนหลอกที่ลงท้ายเหมือนกัน
    "somchai@thaiunion.com.evil.io",
    "somchai@mail.thaiunion.com",    # subdomain ก็ไม่รับ ("เท่านั้น")
    "somchai@thaiunioncom",
])
def test_email_wrong_domain_rejected(raw):
    ok, _email, err = registration.check_email(raw, domains=["thaiunion.com"])
    assert ok is False
    assert err


@pytest.mark.parametrize("raw", ["", "   ", None, "not-an-email",
                                 "a@b", "@thaiunion.com", "a b@thaiunion.com"])
def test_email_bad_format_rejected(raw):
    ok, _email, err = registration.check_email(raw, domains=["thaiunion.com"])
    assert ok is False
    assert err


def test_email_too_long_for_username_column():
    long_local = "x" * 60
    ok, _e, err = registration.check_email(f"{long_local}@thaiunion.com",
                                           domains=["thaiunion.com"])
    assert ok is False
    assert "64" in err


def test_empty_domain_list_allows_any_company():
    ok, email, _err = registration.check_email("a@example.org", domains=[])
    assert ok is True and email == "a@example.org"


def test_rate_limit_blocks_after_cap():
    registration.reset_rate()
    for _ in range(3):
        assert registration.check_rate("10.0.0.1", limit=3)[0] is True
    allowed, retry = registration.check_rate("10.0.0.1", limit=3)
    assert allowed is False and retry > 0
    # คนละ IP ไม่โดนโควตาของคนอื่น
    assert registration.check_rate("10.0.0.2", limit=3)[0] is True


def test_rate_limit_disabled_with_zero():
    registration.reset_rate()
    for _ in range(50):
        assert registration.check_rate("10.0.0.3", limit=0)[0] is True


def test_rate_limit_window_expires():
    registration.reset_rate()
    assert registration.check_rate("10.0.0.4", now=1000.0, limit=1)[0] is True
    assert registration.check_rate("10.0.0.4", now=1001.0, limit=1)[0] is False
    assert registration.check_rate("10.0.0.4", now=1000.0 + 3601, limit=1)[0] is True


# ── ชั้น HTTP ─────────────────────────────────────────────────────────

class FakeStore:
    """แทน SQL Server: จำผู้ใช้ไว้ใน dict."""

    def __init__(self):
        self.users = {}          # username -> dict
        self.roles = {"Viewer": 4, "Admin": 1}
        self.audit = []
        self.next_id = 100
        self.fail_create = False

    def db_available(self):
        return True

    def get_role_id(self, name):
        return self.roles.get(name)

    def get_user_by_login(self, login):
        return self.users.get(login)

    def create_user(self, username, email, password_hash, role_id):
        if self.fail_create or username in self.users:
            raise RuntimeError("duplicate")
        self.next_id += 1
        self.users[username] = {"user_id": self.next_id, "username": username,
                                "email": email, "password_hash": password_hash,
                                "role_id": role_id}
        return self.next_id

    def record_admin_action(self, actor, action, target, detail=""):
        self.audit.append((actor, action, target, detail))


@pytest.fixture
def fake(monkeypatch):
    fs = FakeStore()
    for name in ("db_available", "get_role_id", "get_user_by_login",
                 "create_user", "record_admin_action"):
        monkeypatch.setattr(store, name, getattr(fs, name))
    # bcrypt ไม่จำเป็นต้องมีจริงในเครื่องที่รันเทสต์
    monkeypatch.setattr(passwords, "hashing_available", lambda: True)
    monkeypatch.setattr(passwords, "hash_password", lambda p: "hashed:" + p)
    monkeypatch.setattr(ac, "REGISTER_ENABLED", True)
    monkeypatch.setattr(ac, "REGISTER_ROLE", "Viewer")
    monkeypatch.setattr(ac, "REGISTER_EMAIL_DOMAINS", ("thaiunion.com",))
    monkeypatch.setattr(ac, "REGISTER_MAX_PER_IP_HOUR", 100)
    registration.reset_rate()
    return fs


@pytest.fixture
def client(fake):
    app = Flask(__name__,
                template_folder=os.path.join(_ROOT, "templates"),
                static_folder=os.path.join(_ROOT, "static"))
    app.register_blueprint(auth_bp)
    return app.test_client()


GOOD_PW = "Str0ng!Pass"


def _post(client, **body):
    return client.post("/api/auth/register", json=body)


def test_register_creates_viewer_with_email_as_username(client, fake):
    r = _post(client, email="Somchai.S@ThaiUnion.com", password=GOOD_PW,
              confirm_password=GOOD_PW)
    assert r.status_code == 201
    assert r.get_json()["username"] == "somchai.s@thaiunion.com"

    user = fake.users["somchai.s@thaiunion.com"]
    assert user["email"] == "somchai.s@thaiunion.com"      # username == email
    assert user["role_id"] == fake.roles["Viewer"]         # fix เป็น Viewer
    assert user["password_hash"].startswith("hashed:")     # ไม่เก็บ plain text
    assert fake.audit and fake.audit[0][1] == "self_register"


def test_client_cannot_choose_role(client, fake):
    r = _post(client, email="a@thaiunion.com", password=GOOD_PW, role="Admin")
    assert r.status_code == 201
    assert fake.users["a@thaiunion.com"]["role_id"] == fake.roles["Viewer"]


def test_duplicate_email_rejected(client, fake):
    assert _post(client, email="a@thaiunion.com", password=GOOD_PW).status_code == 201
    r = _post(client, email="A@THAIUNION.com", password=GOOD_PW)   # ต่างแค่ตัวพิมพ์
    assert r.status_code == 409
    assert len(fake.users) == 1


def test_wrong_domain_never_touches_db(client, fake):
    r = _post(client, email="somchai@gmail.com", password=GOOD_PW)
    assert r.status_code == 400
    assert fake.users == {}


@pytest.mark.parametrize("pw", ["Sh0rt!a", "alllowercase1!", "NOLOWER1!",
                                "NoDigits!!", "NoSpecial1A"])
def test_weak_password_rejected(client, fake, pw):
    r = _post(client, email="a@thaiunion.com", password=pw)
    assert r.status_code == 400
    assert fake.users == {}


def test_confirm_mismatch_rejected(client, fake):
    r = _post(client, email="a@thaiunion.com", password=GOOD_PW,
              confirm_password=GOOD_PW + "x")
    assert r.status_code == 400
    assert fake.users == {}


def test_registration_disabled_returns_403(client, fake, monkeypatch):
    monkeypatch.setattr(ac, "REGISTER_ENABLED", False)
    r = _post(client, email="a@thaiunion.com", password=GOOD_PW)
    assert r.status_code == 403
    assert fake.users == {}


def test_missing_role_in_db_is_reported_not_crashed(client, fake):
    fake.roles.pop("Viewer")
    r = _post(client, email="a@thaiunion.com", password=GOOD_PW)
    assert r.status_code == 503
    assert fake.users == {}


def test_db_insert_race_returns_409(client, fake):
    fake.fail_create = True
    r = _post(client, email="a@thaiunion.com", password=GOOD_PW)
    assert r.status_code == 409


def test_rate_limited_after_cap(client, fake, monkeypatch):
    monkeypatch.setattr(ac, "REGISTER_MAX_PER_IP_HOUR", 2)
    registration.reset_rate()
    assert _post(client, email="a@thaiunion.com", password=GOOD_PW).status_code == 201
    assert _post(client, email="b@thaiunion.com", password=GOOD_PW).status_code == 201
    assert _post(client, email="c@thaiunion.com", password=GOOD_PW).status_code == 429
    assert "c@thaiunion.com" not in fake.users


def test_bad_email_does_not_burn_rate_quota(client, fake, monkeypatch):
    monkeypatch.setattr(ac, "REGISTER_MAX_PER_IP_HOUR", 1)
    registration.reset_rate()
    assert _post(client, email="typo@gmail.com", password=GOOD_PW).status_code == 400
    assert _post(client, email="a@thaiunion.com", password=GOOD_PW).status_code == 201


# ── หน้า /login ───────────────────────────────────────────────────────

def test_login_page_shows_register_button(client):
    html = client.get("/login").get_data(as_text=True)
    assert "ลงทะเบียน" in html
    assert 'id="rg-modal"' in html
    assert "thaiunion.com" in html
    # ฟอร์มลงทะเบียนต้องไม่มีช่อง role / ชื่อผู้ใช้
    assert 'id="rg-role"' not in html
    assert 'id="rg-username"' not in html


def test_login_page_hides_register_when_disabled(client, monkeypatch):
    monkeypatch.setattr(ac, "REGISTER_ENABLED", False)
    html = client.get("/login").get_data(as_text=True)
    assert 'id="rg-modal"' not in html
    assert 'id="register-open"' not in html


# ── ด่านสิทธิ์ ────────────────────────────────────────────────────────

def test_register_endpoint_is_public():
    """ถ้าไม่อยู่ใน _PUBLIC_PATHS ผู้ที่ยังไม่ล็อกอินจะโดน 401 = ปุ่มลงทะเบียนใช้ไม่ได้."""
    assert _is_public("/api/auth/register") is True
    assert _required_permission("/api/auth/register") is None


def test_user_management_api_still_needs_manage_users():
    """ของเดิมต้องไม่หลุดตามไปเป็น public."""
    assert _is_public("/api/auth/users") is False
    assert _required_permission("/api/auth/users") == "manage_users"
