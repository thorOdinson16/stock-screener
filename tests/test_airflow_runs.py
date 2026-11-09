from api import airflow


def test_list_dag_runs_requests_newest_first(monkeypatch):
    calls = []

    def request(method, path):
        calls.append((method, path))
        return {"dag_runs": [{"dag_run_id": "latest"}]}

    monkeypatch.setattr(airflow, "_request", request)
    assert airflow.list_dag_runs("screening_on_demand", limit=5) == [{"dag_run_id": "latest"}]
    assert calls == [("GET", "/dags/screening_on_demand/dagRuns?limit=5&order_by=-logical_date")]
