from __future__ import annotations

import threading
import time

import pytest
from pydantic import BaseModel

from app.core.db import DocumentStore, close_store, get_store


@pytest.fixture
def store(tmp_path):
    document_store = DocumentStore(tmp_path / "store" / "app.db")
    yield document_store
    document_store.close()


def test_put_get_and_timestamps(store):
    created = store.put("reports", "rep_1", {"status": "live", "title": "Työmaa"})
    assert created.data == {"status": "live", "title": "Työmaa"}
    assert created.created_at == created.updated_at
    assert created.created_at.endswith("Z") and "T" in created.created_at

    time.sleep(0.005)
    replaced = store.put("reports", "rep_1", {"status": "sent"})
    assert replaced.data == {"status": "sent"}
    assert replaced.created_at == created.created_at
    assert replaced.updated_at > created.updated_at

    fetched = store.get("reports", "rep_1")
    assert fetched == replaced
    assert store.get("reports", "missing") is None


def test_collections_are_separate(store):
    store.put("reports", "a", {"n": 1})
    store.put("types", "a", {"n": 2})
    assert store.get("reports", "a").data == {"n": 1}
    assert store.get("types", "a").data == {"n": 2}
    assert store.count("reports") == 1
    assert [doc.id for doc in store.list("types")] == ["a"]


def test_list_is_newest_first_with_limit_and_offset(store):
    for number in range(5):
        store.put("reports", f"rep_{number}", {"n": number})
    assert [doc.id for doc in store.list("reports")] == [f"rep_{n}" for n in (4, 3, 2, 1, 0)]
    assert [doc.id for doc in store.list("reports", limit=2)] == ["rep_4", "rep_3"]
    assert [doc.id for doc in store.list("reports", limit=2, offset=2)] == ["rep_2", "rep_1"]
    assert [doc.id for doc in store.list("reports", newest_first=False)][0] == "rep_0"
    assert store.list("nothing") == []

    time.sleep(0.005)
    store.put("reports", "rep_1", {"n": 10})
    assert store.list("reports", order_by="updated_at")[0].id == "rep_1"
    assert store.list("reports")[0].id == "rep_4"  # created order is unchanged
    with pytest.raises(ValueError):
        store.list("reports", order_by="data; DROP TABLE documents")


def test_delete(store):
    store.put("reports", "rep_1", {})
    assert store.delete("reports", "rep_1") is True
    assert store.delete("reports", "rep_1") is False
    assert store.get("reports", "rep_1") is None


def test_pydantic_models_are_stored_as_json(store):
    class Note(BaseModel):
        title: str
        tags: list[str]

    store.put("notes", "n1", Note(title="Havainto", tags=["a", "b"]))
    assert store.get("notes", "n1").data == {"title": "Havainto", "tags": ["a", "b"]}
    with pytest.raises(TypeError):
        store.put("notes", "n2", ["not", "a", "dict"])


def test_update_changes_one_document_and_reports_a_missing_one(store):
    store.put("reports", "rep_1", {"count": 0})
    updated = store.update("reports", "rep_1", lambda data: {**data, "count": 1})
    assert updated.data == {"count": 1}

    def in_place(data):
        data["count"] += 1

    assert store.update("reports", "rep_1", in_place).data == {"count": 2}
    assert store.update("reports", "missing", lambda data: data) is None


def test_worker_threads_do_not_lose_updates(store):
    store.put("counters", "c", {"count": 0})
    threads_count, rounds = 8, 25
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            for step in range(rounds):
                store.update("counters", "c", lambda data: {"count": data["count"] + 1})
                store.put("scratch", f"{worker}-{step}", {"worker": worker})
                store.list("scratch", limit=5)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=work, args=(number,)) for number in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert store.get("counters", "c").data == {"count": threads_count * rounds}
    assert store.count("scratch") == threads_count * rounds


def test_transaction_rolls_back_on_error(store):
    store.put("reports", "rep_1", {"status": "live"})
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put("reports", "rep_1", {"status": "sent"})
            store.put("reports", "rep_2", {"status": "live"})
            raise RuntimeError("stop")
    assert store.get("reports", "rep_1").data == {"status": "live"}
    assert store.get("reports", "rep_2") is None


def test_database_uses_wal_and_survives_a_reopen(tmp_path):
    path = tmp_path / "app.db"
    first = DocumentStore(path)
    first.put("reports", "rep_1", {"status": "live"})
    with first.transaction() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    first.close()

    second = DocumentStore(path)
    assert second.get("reports", "rep_1").data == {"status": "live"}
    second.close()


def test_process_wide_store_lives_in_data_dir(settings):
    store = get_store()
    assert store is get_store()
    assert store.path == settings.data_path / "app.db"
    store.put("reports", "rep_1", {})
    assert store.path.is_file()
    close_store()
    assert get_store() is not store
