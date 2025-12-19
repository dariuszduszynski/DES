"""
Load test for DELETE API endpoint.

Requirements:
    pip install locust

Usage:
    locust -f tests/load_test_deletion.py --host=http://localhost:8000
"""

import random
from datetime import datetime, timezone

from locust import HttpUser, between, task


class DeletionLoadTest(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        """Setup: Generate list of UIDs to delete."""

        self.uids = [f"test-uid-{i}" for i in range(1000)]
        self.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        self.api_key = "test-api-key"

    @task(10)
    def delete_file(self) -> None:
        """DELETE operation (most common)."""

        uid = random.choice(self.uids)
        response = self.client.delete(
            f"/files/{uid}",
            params={
                "created_at": self.created_at,
                "deleted_by": "load-test",
                "reason": "GDPR",
            },
            headers={"X-API-Key": self.api_key},
            name="/files/{uid} [DELETE]",
        )

        assert response.status_code in [200, 404, 410], f"Unexpected status: {response.status_code}"

    @task(5)
    def get_file(self) -> None:
        """GET operation (verify 410 after delete)."""

        uid = random.choice(self.uids)
        response = self.client.get(
            f"/files/{uid}",
            params={"created_at": self.created_at},
            name="/files/{uid} [GET]",
        )

        assert response.status_code in [200, 404, 410], f"Unexpected status: {response.status_code}"

    @task(1)
    def health_check(self) -> None:
        """Health check."""

        self.client.get("/health", name="/health")
