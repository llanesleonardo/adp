from tests.conftest import sign_in


def test_ask_api_creates_thread_and_follow_up(client, accounts):
    sign_in(client, accounts["editor"])
    created_doc = client.post(
        "/api/v1/documents",
        json={
            "title": "Inventory reservation",
            "body": "Use inventory reservation to prevent overselling in checkout.",
            "publish": True,
        },
    )
    assert created_doc.status_code == 201, created_doc.text

    sign_in(client, accounts["viewer"])
    created = client.post("/api/v1/ask/threads", json={"question": "overselling"})
    assert created.status_code == 201, created.text
    thread = created.json()
    assert thread["title"] == "overselling"
    assert len(thread["messages"]) == 2
    assert thread["messages"][0]["role"] == "user"
    assert thread["messages"][1]["role"] == "assistant"
    assert "Inventory reservation" in thread["messages"][1]["body"]

    listed = client.get("/api/v1/ask/threads")
    assert listed.status_code == 200
    assert any(row["id"] == thread["id"] for row in listed.json())

    follow = client.post(
        f"/api/v1/ask/threads/{thread['id']}/messages",
        json={"question": "What else should I check?"},
    )
    assert follow.status_code == 201, follow.text
    assert len(follow.json()["messages"]) == 4

    assistant_id = follow.json()["messages"][1]["id"]
    favorited = client.post(f"/api/v1/ask/threads/{thread['id']}/messages/{assistant_id}/favorite")
    assert favorited.status_code == 200
    assert favorited.json()["messages"][1]["favorite"] is True

    sign_in(client, accounts["editor"])
    assert client.get(f"/api/v1/ask/threads/{thread['id']}").status_code == 404
