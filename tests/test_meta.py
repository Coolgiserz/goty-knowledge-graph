"""元数据接口测试：/api/meta、/api/boards、/graph 重定向。"""


def test_meta_disabled_reports_exploration_off(client_disabled):
    r = client_disabled.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["exploration_enabled"] is False
    assert "counts" in body and "sha256" in body
    # 板块清单始终可访问（与开关无关）
    assert isinstance(body["boards"], list) and len(body["boards"]) > 0


def test_meta_enabled_reports_exploration_on(client_enabled):
    r = client_enabled.get("/api/meta")
    assert r.status_code == 200
    assert r.json()["exploration_enabled"] is True


def test_boards_schema_well_formed(client_disabled):
    r = client_disabled.get("/api/boards")
    assert r.status_code == 200
    boards = r.json()["boards"]
    assert len(boards) > 0
    first = boards[0]
    # response_model 约束下的关键字段齐全
    for key in (
        "name",
        "label",
        "description",
        "params",
        "interpretation",
        "interpretation_defaults",
    ):
        assert key in first
    # 参数声明字段齐全
    if first["params"]:
        p = first["params"][0]
        for key in ("key", "label", "type", "default"):
            assert key in p


def test_graph_redirects_to_root(client_disabled):
    # 旧书签 /graph/ 跳回根（v1 原始页）
    r = client_disabled.get("/graph/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/"


def test_openapi_available(client_disabled):
    r = client_disabled.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ("/api/meta", "/api/boards"):
        assert p in paths
