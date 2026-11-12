import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="esprel-test-")
os.environ["INSTANCE_DIR"] = _tmp
os.environ["SQLITE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")
os.environ["AUTO_MIGRATE"] = "1"
os.environ["SEED_DEMO_USERS"] = "1"
os.environ["SUPABASE_URL"] = ""
os.environ["LDAP_URL"] = ""

if os.environ["DATABASE_URL"]:  # เริ่มจากฐานข้อมูลว่างทุกครั้ง
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")


@pytest.fixture(scope="session")
def app():
    from app import app as flask_app
    flask_app.config["TESTING_NO_CSRF"] = True
    return flask_app


def login(client, username, password):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302, r.data[:500]
    return client
