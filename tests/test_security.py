"""安全防护测试：两档限流(429)、黑名单(403)、探索令牌鉴权(401/200)、
客户端 IP 识别（XFF 伪造防护）与审计脱敏。
"""

import json

from api.middleware import _redact_query, _redact_sensitive
from api.ratelimit import get_client_ip


def test_general_rate_limit_triggers_429(client_ratelimit):
    # 阈值被 fixture 调小为 3；前 3 次通过，第 4 次被限流
    codes = [client_ratelimit.get("/api/meta").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    assert client_ratelimit.get("/api/meta").headers.get("retry-after")


def test_blacklisted_ip_blocked(client_blacklist, client_disabled):
    # 命中黑名单种子（直连对端 "testclient"）-> 403
    r = client_blacklist.get("/api/meta")
    assert r.status_code == 403
    # 未列入黑名单的客户端不受影响
    r2 = client_disabled.get("/api/meta")
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


# --------------------------------------------------------------------------- #
# 客户端 IP 识别：X-Forwarded-For 客户端可任意伪造，只能在直连对端可信时才采信
# --------------------------------------------------------------------------- #
class _FakeRequest:
    """最小请求替身：只需 client 与 headers 两个属性。"""

    def __init__(self, host: str = "10.0.0.1", headers=None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}


def test_client_ip_ignores_forged_xff_when_peer_untrusted():
    """直连对端不是可信代理时，伪造的 XFF 必须被忽略（否则限流/黑名单/封禁全可绕过）。"""
    req = _FakeRequest("203.0.113.9", {"x-forwarded-for": "1.2.3.4"})
    # 未配置可信代理 -> 一律不采信
    assert get_client_ip(req, trust_proxy=True, trusted_proxies="") == "203.0.113.9"
    # 配了可信代理但对端不在其中 -> 同样不采信
    assert get_client_ip(req, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "203.0.113.9"


def test_client_ip_honors_xff_when_peer_is_trusted():
    """直连对端确实是可信代理时，采信 XFF（由右向左跳过代理自身追加的条目）。"""
    req = _FakeRequest("10.0.0.1", {"x-forwarded-for": "9.9.9.9, 10.0.0.1"})
    assert get_client_ip(req, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "9.9.9.9"
    # 多级代理链：最左是原始客户端，右侧两跳均为可信代理 -> 取最左
    chain = _FakeRequest("10.0.0.1", {"x-forwarded-for": "9.9.9.9, 10.0.0.5, 10.0.0.1"})
    assert get_client_ip(chain, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "9.9.9.9"


def test_client_ip_rejects_invalid_and_malformed_values():
    """格式非法 / 全链皆可信 / trust_proxy 关闭时，回落到直连对端。"""
    bad = _FakeRequest("10.0.0.1", {"x-forwarded-for": "not-an-ip"})
    assert get_client_ip(bad, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "10.0.0.1"
    # 全链都是可信代理（无真实客户端信息）-> 回落
    allproxy = _FakeRequest("10.0.0.1", {"x-forwarded-for": "10.0.0.5, 10.0.0.1"})
    assert get_client_ip(allproxy, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "10.0.0.1"
    # 显式关闭 trust_proxy
    off = _FakeRequest("10.0.0.1", {"x-forwarded-for": "9.9.9.9"})
    assert get_client_ip(off, trust_proxy=False, trusted_proxies="10.0.0.0/8") == "10.0.0.1"


def test_x_real_ip_only_trusted_when_peer_trusted():
    req = _FakeRequest("203.0.113.9", {"x-real-ip": "1.2.3.4"})
    assert get_client_ip(req, trust_proxy=True, trusted_proxies="") == "203.0.113.9"
    trusted = _FakeRequest("10.0.0.1", {"x-real-ip": "1.2.3.4"})
    assert get_client_ip(trusted, trust_proxy=True, trusted_proxies="10.0.0.0/8") == "1.2.3.4"


# --------------------------------------------------------------------------- #
# 审计脱敏：query 与 body 都不能落明文凭据
# --------------------------------------------------------------------------- #
def test_redact_query_masks_token():
    """?token= / ?password= 等凭据参数的值必须被遮蔽（管理报表与验证链接都走 query）。"""
    assert _redact_query("token=abc123") == "token=***"
    assert _redact_query("a=1&token=abc&b=2") == "a=1&token=***&b=2"
    assert _redact_query("") == ""
    # 非敏感参数原样保留
    assert _redact_query("page=2&size=10") == "page=2&size=10"


def test_redact_sensitive_masks_truncated_json():
    """body 被截断在密码中间时也必须遮蔽——回退正则不能要求闭合引号。"""
    truncated = '{"username":"alice","password":"P@ssw0rd-leaked-prefix'
    out = _redact_sensitive(truncated)
    assert "P@ssw0rd" not in out, f"截断 body 未脱敏：{out}"
    assert "***" in out


def test_redact_sensitive_masks_complete_json():
    out = _redact_sensitive('{"username":"alice","password":"P@ssw0rd1"}')
    assert "P@ssw0rd1" not in out
    # 非敏感字段保留（json.dumps 默认带空格，故按解析后的值断言而非字面量）
    assert json.loads(out) == {"username": "alice", "password": "***"}
