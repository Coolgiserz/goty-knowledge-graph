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
async def test_meta_p95_and_throughput():
    app = create_app(Settings(enable_exploration=False))
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
    assert p95 < 500, f"p95 过高: {p95:.1f}ms"
    assert rps > 30, f"吞吐过低: {rps:.0f} rps"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_boards_p95_and_throughput():
    app = create_app(Settings(enable_exploration=False))
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
    assert p95 < 500, f"p95 过高: {p95:.1f}ms"
    assert rps > 30, f"吞吐过低: {rps:.0f} rps"
