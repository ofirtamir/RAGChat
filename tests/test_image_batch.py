"""Pictures read through the Batch API: queue, request file, answers into the cache, retries."""
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from src import image_batch, image_reader  # noqa: E402
from src.pdf_parser import parse_pdf  # noqa: E402

TABLE = "| שווי | שנה |\n| --- | --- |\n| 19,200 | 2024 |"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(image_batch, "DB_PATH", tmp_path / "image_batch.db")
    monkeypatch.setattr(image_batch, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(image_reader, "CACHE_PATH", tmp_path / "image_reads.db")


def _table_png(seed: int) -> bytes:
    h, w = 160, 480
    a = np.full((h, w, 3), 255, np.uint8)
    for y in range(0, h, 32):
        a[y:y + 4, :, :] = 0
    for x in range(0, w, 96 - seed):
        a[:, x:x + 4, :] = 0
    return pymupdf.Pixmap(pymupdf.csRGB, w, h, a.tobytes(), 0).tobytes("png")


def _pdf(path: Path, seeds) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 60), "Intro")
    for i, seed in enumerate(seeds):
        page.insert_image(pymupdf.Rect(60, 100 + i * 200, 540, 260 + i * 200), stream=_table_png(seed))
    doc.save(path)
    return path


class FakeClient:
    """files.upload / batches.create / batches.get / files.download, answering every request with `answer`."""

    def __init__(self, answer=None, state="JOB_STATE_SUCCEEDED"):
        self.uploads, self.answer, self.state = [], answer, state
        self.files = SimpleNamespace(upload=self._upload, download=self._download)
        self.batches = SimpleNamespace(create=self._create, get=self._get)

    def _upload(self, file, config):
        self.uploads.append([json.loads(ln) for ln in Path(file).read_text(encoding="utf-8").splitlines()])
        return SimpleNamespace(name=f"files/in{len(self.uploads)}")

    def _create(self, model, src, config):
        assert model == image_reader.MODEL
        return SimpleNamespace(name=f"batches/{src.split('/')[-1]}")

    def _get(self, name):
        return SimpleNamespace(state=SimpleNamespace(name=self.state), error=None,
                               dest=SimpleNamespace(file_name=name.replace("batches/", "files/out-")))

    def _download(self, file):
        requests = self.uploads[int(file.split("in")[-1]) - 1]
        return "\n".join(json.dumps(self.answer(r["key"])) for r in requests).encode("utf-8")


def ok(text, finish="STOP"):
    return lambda key: {"key": key, "response": {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 400}}}


def _queue(tmp_path, key="doc1", seeds=(0,), name="a.pdf"):
    pdf = _pdf(tmp_path / name, seeds)
    return image_batch.collect(key, pdf, parse_pdf(pdf, image_markers=True))


def test_request_has_the_online_prompt_and_settings(tmp_path):
    assert _queue(tmp_path) == 1
    client = FakeClient()
    assert image_batch.submit(client) == [{"name": "batches/in1", "pictures": 1}]
    (line,) = client.uploads[0]
    parts = line["request"]["contents"][0]["parts"]
    assert base64.b64decode(parts[0]["inline_data"]["data"]).startswith(b"\x89PNG")
    assert parts[1]["text"] == image_reader.PROMPT
    cfg = line["request"]["generation_config"]
    assert cfg["temperature"] == 0 and cfg["max_output_tokens"] == image_reader.MAX_OUTPUT_TOKENS
    assert cfg["media_resolution"] == "MEDIA_RESOLUTION_HIGH"
    assert image_batch.submit(client) == []                     # nothing is sent twice
    assert not list((tmp_path / "work").glob("*.jsonl"))        # request files are not kept


def test_answers_go_into_the_cache_and_the_document_becomes_ready(tmp_path):
    _queue(tmp_path)
    client = FakeClient(ok(TABLE))
    image_batch.submit(client)
    assert image_batch.status()["in_jobs"] == 1 and image_batch.ready_keys() == []
    (job,) = image_batch.collect_results(client)
    assert job["answered"] == 1 and job["ready"] == ["doc1"]
    price_in, price_out = image_reader._price(image_reader.MODEL)
    assert job["cost_usd"] == pytest.approx((1000 * price_in + 400 * price_out) / 1e6 / 2)
    digest = client.uploads[0][0]["key"]
    assert image_reader.cached(digest) == f"{image_reader.LABEL}\n\n{TABLE}"
    assert image_batch.ready_keys() == ["doc1"] and image_batch.status()["waiting_documents"] == 0
    assert _queue(tmp_path) == 0                                # cached now: never queued again
    image_batch.clear_ready(["doc1"])
    assert image_batch.ready_keys() == []


def test_none_and_cut_off_answers_are_cached_as_nothing_to_read():
    assert image_batch.parse_result(ok("NONE")("k"))[0] == ""
    assert image_batch.parse_result(ok(TABLE, "MAX_TOKENS")("k"))[0] == ""
    thought = {"key": "k", "response": {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": TABLE}]}}]}}
    assert image_batch.parse_result(thought)[0].endswith(TABLE)
    assert image_batch.parse_result({"key": "k", "error": {"code": 500}})[0] is None


def test_the_same_picture_in_two_documents_is_sent_once(tmp_path):
    _queue(tmp_path, "doc1", name="a.pdf")
    _queue(tmp_path, "doc2", name="b.pdf")
    client = FakeClient(ok(TABLE))
    assert image_batch.submit(client)[0]["pictures"] == 1
    assert image_batch.collect_results(client)[0]["ready"] == ["doc1", "doc2"]


def test_failed_requests_are_sent_again_then_given_up(tmp_path):
    _queue(tmp_path, seeds=(0, 20))
    failing = FakeClient(lambda key: {"key": key, "error": {"code": 500, "message": "internal"}})
    image_batch.submit(failing)
    assert image_batch.collect_results(failing)[0]["failed"] == 2
    assert image_batch.status()["pending_pictures"] == 2         # back in the queue
    image_batch.submit(failing)
    assert image_batch.collect_results(failing)[0]["ready"] == ["doc1"]   # given up: parse without them
    assert image_batch.status()["waiting_documents"] == 0


def test_a_failed_job_puts_its_pictures_back(tmp_path):
    _queue(tmp_path)
    client = FakeClient(state="JOB_STATE_EXPIRED")
    image_batch.submit(client)
    assert image_batch.collect_results(client)[0]["state"] == "failed"
    assert image_batch.status()["pending_pictures"] == 1
    assert image_batch.status()["jobs"][0]["state"] == "failed"


def test_a_running_job_is_left_alone(tmp_path):
    _queue(tmp_path)
    client = FakeClient(state="JOB_STATE_RUNNING")
    image_batch.submit(client)
    assert image_batch.collect_results(client) == []
    assert image_batch.status()["in_jobs"] == 1
