from tests.conftest import sign_in


def test_ingest_jobs_list_endpoint(client, accounts):
    sign_in(client, accounts["plus"])
    created = client.post(
        "/api/v1/ingest/jobs",
        json={"title": "Ingested note", "body": "from a source", "publish": False},
    )
    assert created.status_code == 201, created.text

    listed = client.get("/api/v1/ingest/jobs")
    assert listed.status_code == 200, listed.text
    jobs = listed.json()
    assert any(row["title"] == "Ingested note" for row in jobs)
    assert all(
        {
            "id",
            "status",
            "title",
            "body",
            "source_label",
            "source_url",
            "publish",
            "document_id",
            "error",
            "created_at",
            "finished_at",
        }
        <= set(row)
        for row in jobs
    )

    sign_in(client, accounts["viewer"])
    denied = client.get("/api/v1/ingest/jobs")
    assert denied.status_code in {200, 403}
    if denied.status_code == 200:
        assert denied.json() == []
