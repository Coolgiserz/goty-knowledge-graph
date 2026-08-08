"""接口性能测试：进程内并发压测（httpx + ASGITransport），断言 p95 延迟与吞吐。

目标接口均为「廉价只读」（``/api/meta``、``/api/boards``），不触发重计算，适合作为
回归门禁；阈值留足余量，避免在 CI 弱机上偶发抖动。重负载探索接口的性能请改用
``tests/perf/locustfile.py`` 对准运行中的服务做手动压测。

用 ``pytest -m perf`` 单独运行本文件。
"""

import asyncio
import time

import httpx
import pytest
from api.app import create_app
from api.config import Settings

CONCURRENCY = 40
TOTAL_REQUESTS = 200


@pytest.mark.perf
@pytest.mark.asyncio
async def test_meta_p95_and_throughput(tmp_path):
    # 性能测试只测「廉价只读接口」的稳态吞吐，需排除异常判定/限流/UA 拦截/审计写库干扰
    # （审计写库吞吐受 SQLite 单文件并发写串行化影响，属于部署/DB 选型问题，非接口逻辑
    #  问题；审计正确性与并发由 tests/test_audit.py 覆盖，重负载写入请用 locustfile.py）。
    app = create_app(
        Settings(
            enable_exploration=False,
            anomaly_enabled=False,
            rate_limit_max=TOTAL_REQUESTS + 50,
            rate_window=60,
            block_bot_ua=False,  # httpx 默认 UA 为 python-httpx，关拦截以测纯吞吐
            audit_enabled=False,
        )
    )
    transport = httpx.ASGITransport(app=app)
    latencies: list[float] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one() -> None:
        async with sem:
            t0 = time.perf_counter()
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.get("/api/meta")
            dt = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200
            latencies.append(dt)

    start = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(TOTAL_REQUESTS)])
    wall = time.perf_counter() - start

    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    rps = TOTAL_REQUESTS / wall

    print(f"\n[perf] meta: N={TOTAL_REQUESTS} conc={CONCURRENCY} p95={p95:.1f}ms rps≈{rps:.0f}")
    # 阈值留足余量：本门禁只捕获「接口逻辑」层面的严重回退（如同步阻塞、N+1 查询），
    # 不用于对绝对延迟做精确 SLA 断言——弱 CI 机在 40 路并发下调度抖动即可使 p95 过百毫秒级。
    # 重负载/DB 写入吞吐请改用 locustfile.py 对运行中的服务压测。
    assert p95 < 2000, f"p95 过高: {p95:.1f}ms"
    assert rps > 30, f"吞吐过低: {rps:.0f} rps"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_boards_p95_and_throughput(tmp_path):
    # 性能测试只测「廉价只读接口」的稳态吞吐，需排除异常判定/限流/UA 拦截/审计写库干扰
    # （审计写库吞吐受 SQLite 单文件并发写串行化影响，属于部署/DB 选型问题，非接口逻辑
    #  问题；审计正确性与并发由 tests/test_audit.py 覆盖，重负载写入请用 locustfile.py）。
    app = create_app(
        Settings(
            enable_exploration=False,
            anomaly_enabled=False,
            rate_limit_max=TOTAL_REQUESTS + 50,
            rate_window=60,
            block_bot_ua=False,  # httpx 默认 UA 为 python-httpx，关拦截以测纯吞吐
            audit_enabled=False,
        )
    )
    transport = httpx.ASGITransport(app=app)
    latencies: list[float] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one() -> None:
        async with sem:
            t0 = time.perf_counter()
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.get("/api/boards")
            dt = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200
            latencies.append(dt)

    start = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(TOTAL_REQUESTS)])
    wall = time.perf_counter() - start

    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    rps = TOTAL_REQUESTS / wall

    print(f"\n[perf] boards: N={TOTAL_REQUESTS} conc={CONCURRENCY} p95={p95:.1f}ms rps≈{rps:.0f}")
    # 阈值留足余量：本门禁只捕获「接口逻辑」层面的严重回退（如同步阻塞、N+1 查询），
    # 不用于对绝对延迟做精确 SLA 断言——弱 CI 机在 40 路并发下调度抖动即可使 p95 过百毫秒级。
    # 重负载/DB 写入吞吐请改用 locustfile.py 对运行中的服务压测。
    assert p95 < 2000, f"p95 过高: {p95:.1f}ms"
    assert rps > 30, f"吞吐过低: {rps:.0f} rps"
