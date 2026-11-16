"""ทดสอบ LDAP ด้วย ldap3 MOCK server"""
from ldap3 import MOCK_SYNC, OFFLINE_SLAPD_2_4, Connection, Server


def _mock_server():
    srv = Server("mock", get_info=OFFLINE_SLAPD_2_4)
    c = Connection(srv, user="cn=admin,dc=nu,dc=ac,dc=th", password="x", client_strategy=MOCK_SYNC)
    c.strategy.add_entry("uid=somchai,ou=people,dc=nu,dc=ac,dc=th",
                         {"uid": "somchai", "userPassword": "secret", "displayName": "สมชาย ใจดี",
                          "mail": "somchai@nu.ac.th", "ou": "คณะวิทยาศาสตร์", "objectClass": "person"})
    return srv, c


def test_ldap_bind(monkeypatch):
    from ldap3 import Connection as RealConn

    import auth_ldap
    import config
    srv, mock = _mock_server()
    monkeypatch.setattr(config, "LDAP_URL", "ldap://mock")
    monkeypatch.setattr(config, "LDAP_BASE_DN", "dc=nu,dc=ac,dc=th")
    monkeypatch.setattr(config, "LDAP_USER_DN_TEMPLATE", "uid={username},ou=people,dc=nu,dc=ac,dc=th")
    monkeypatch.setattr(config, "LDAP_USER_FILTER", "(uid={username})")
    monkeypatch.setattr(config, "LDAP_ATTR_DEPT", "ou")

    def fake_conn(server, **kw):
        kw.pop("receive_timeout", None)
        return RealConn(server, client_strategy=MOCK_SYNC, **kw)
    monkeypatch.setattr("ldap3.Connection", fake_conn)
    # ให้ mock server ใช้ข้อมูลชุดเดียวกัน
    import ldap3.strategy.mockBase as mb  # noqa
    info = auth_ldap.authenticate("somchai", "secret", server=mock.server)
    assert info and info["full_name"] == "สมชาย ใจดี" and info["faculty"] == "คณะวิทยาศาสตร์"
    assert auth_ldap.authenticate("somchai", "wrong", server=mock.server) is None
    assert auth_ldap.authenticate("somchai", "", server=mock.server) is None
