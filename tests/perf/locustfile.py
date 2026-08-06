"""手动大流量压测脚本（Locust）。

仅在需要对「运行中的服务」做大流量压测时使用，不进入 pytest 自动收集：

    uv run locust -f tests/perf/locustfile.py --host http://127.0.0.1:8080 -u 50 -r 10 -t 60s

默认目标为廉价只读接口；若服务开启了探索（``GOTY_ENABLE_EXPLORATION=true``），
可临时放开 ``/api/board/community`` 的权重做重计算压测。
"""

from locust import HttpUser, between, task


class ApiUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(5)
    def meta(self):
        self.client.get("/api/meta")

    @task(3)
    def boards(self):
        self.client.get("/api/boards")

    # 重计算接口：仅当目标服务开启探索时才有意义，默认注释。
    # @task(1)
    # def board_sync(self):
    #     self.client.post("/api/board/community", json={"params": {}})
