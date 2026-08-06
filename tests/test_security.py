"""安全防护测试：两档限流(429)、黑名单(403)、探索令牌鉴权(401/200)。"""


def test_general_rate_limit_triggers_429(client_ratelimit):
    # 阈值被 fixture 调小为 3；前 3 次通过，第 4 次被限流
    codes = [client_ratelimit.get("/api/meta").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    assert client_ratelimit.get("/api/meta").headers.get("retry-after")


def test_blacklisted_ip_blocked(client_blacklist):
    # 伪造 X-Forwarded-For 命中黑名单种子
    r = client_blacklist.get("/api/meta", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 403
    # 正常 IP 不受影响
    r2 = client_blacklist.get("/api/meta")
    assert r2.status_code == 200


def test_explore_token_required(client_token):
    # 未带令牌提交任务 -> 401
    r = client_token.post("/api/jobs", json={"board": "community", "params": {}})
    assert r.status_code == 401

    # 错误令牌 -> 401
    r2 = client_token.post(
        "/api/jobs", json={"board": "community", "params": {}}, headers={"x-explore-token": "wrong"}
    )
    assert r2.status_code == 401

    # 正确令牌 -> 200
    r3 = client_token.post(
        "/api/jobs",
        json={"board": "community", "params": {}},
        headers={"x-explore-token": "secret-token"},
    )
    assert r3.status_code == 200
    assert r3.json()["owner"] == "admin"
