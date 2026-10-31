import io
import json
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings
from db import get_session
from main import app
from ocr_training.models import OcrDataset, OcrModel, OcrSample, OcrTrainingJob

FAKE_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9"
)
FAKE_ONNX = b"ONNX_MODEL_BINARY_DATA"


@pytest.fixture
def setup_ocr_dirs(tmp_path: Path):
    photos_dir = tmp_path / "photos"
    models_dir = tmp_path / "models"
    photos_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    token = "secret-trainer-token-123"
    configured = get_settings().model_copy(
        update={
            "ocr_photos_dir": photos_dir,
            "ocr_models_dir": models_dir,
            "ocr_trainer_token": token,
        }
    )
    app.dependency_overrides[get_settings] = lambda: configured
    yield photos_dir, models_dir, token
    app.dependency_overrides.pop(get_settings, None)


@pytest_asyncio.fixture
async def admin_client(client_factory, make_user, session: AsyncSession) -> AsyncClient:
    admin_id = await make_user("admin@example.com")
    return client_factory(admin_id)


@pytest_asyncio.fixture
async def user_client(client_factory, make_user, session: AsyncSession) -> AsyncClient:
    user_id = await make_user("testuser@example.com")
    return client_factory(user_id)


@pytest_asyncio.fixture
async def trainer_client(session: AsyncSession, setup_ocr_dirs) -> AsyncClient:
    _, _, token = setup_ocr_dirs
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    client = AsyncClient(transport=transport, base_url="http://test", headers=headers)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_import_samples_and_idempotence(
    admin_client: AsyncClient, setup_ocr_dirs, session: AsyncSession
):
    photos_dir, _, _ = setup_ocr_dirs

    # Create folder structure: 2024-05 with two valid pairs and one orphan jpg
    folder = photos_dir / "2024-05"
    folder.mkdir(parents=True, exist_ok=True)

    # Sample 1
    (folder / "img1.jpg").write_bytes(FAKE_JPEG)
    (folder / "img1.json").write_text(
        json.dumps(
            {
                "sys": 120,
                "dia": 80,
                "pul": 70,
                "timestamp": "2024-05-01T10:00:00Z",
                "device_model": "Omron M3",
                "source": "telegram",
                "corrected_by_user": True,
                "session": "sess1",
            }
        ),
        encoding="utf-8",
    )

    # Sample 2 (missing session, should fallback to 2024-05)
    (folder / "img2.jpg").write_bytes(FAKE_JPEG)
    (folder / "img2.json").write_text(
        json.dumps(
            {
                "sys": 130,
                "dia": 85,
                "pul": 75,
                "timestamp": 1714560000,
                "device_model": "Microlife",
                "source": "manual",
                "corrected_by_user": False,
            }
        ),
        encoding="utf-8",
    )

    # Orphan jpg (no sidecar)
    (folder / "orphan.jpg").write_bytes(FAKE_JPEG)

    # Ignored folder
    ignored_folder = photos_dir / "labels"
    ignored_folder.mkdir(parents=True, exist_ok=True)
    (ignored_folder / "ignored.jpg").write_bytes(FAKE_JPEG)
    (ignored_folder / "ignored.json").write_text("{}", encoding="utf-8")

    # 1. First import run: 2 created, 0 updated, 1 skipped (orphan)
    res = await admin_client.post("/admin/ocr-training/samples/import")
    assert res.status_code == 200
    data = res.json()
    assert data == {"created": 2, "updated": 0, "skipped": 1}

    # Verify rows in DB
    samples = (await session.exec(select(OcrSample).order_by(OcrSample.photo_path))).all()
    assert len(samples) == 2
    s1, s2 = samples
    assert s1.photo_path == "2024-05/img1.jpg"
    assert s1.ref_sys == 120
    assert s1.ref_dia == 80
    assert s1.ref_pul == 70
    assert s1.session == "sess1"
    assert s1.corrected_by_user is True
    assert s1.device_model == "Omron M3"
    assert s1.source == "telegram"
    assert s1.taken_at is not None

    assert s2.photo_path == "2024-05/img2.jpg"
    assert s2.ref_sys == 130
    assert s2.session == "2024-05"  # Fallback to folder name
    assert s2.corrected_by_user is False

    # 2. Second import run without changes: 0 created, 0 updated, 3 skipped (2 unchanged + 1 orphan)
    res2 = await admin_client.post("/admin/ocr-training/samples/import")
    assert res2.status_code == 200
    assert res2.json() == {"created": 0, "updated": 0, "skipped": 3}

    # 3. Update sidecar and run again: 0 created, 1 updated, 2 skipped
    (folder / "img1.json").write_text(
        json.dumps(
            {
                "sys": 125,
                "dia": 82,
                "pul": 72,
                "timestamp": "2024-05-01T10:00:00Z",
                "device_model": "Omron M3",
                "source": "telegram",
                "corrected_by_user": True,
                "session": "sess1",
            }
        ),
        encoding="utf-8",
    )
    res3 = await admin_client.post("/admin/ocr-training/samples/import")
    assert res3.status_code == 200
    assert res3.json() == {"created": 0, "updated": 1, "skipped": 2}

    await session.refresh(s1)
    assert s1.ref_sys == 125


@pytest.mark.asyncio
async def test_samples_filtering_and_get(admin_client: AsyncClient, setup_ocr_dirs):
    photos_dir, _, _ = setup_ocr_dirs
    folder = photos_dir / "2024-06"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "a.jpg").write_bytes(FAKE_JPEG)
    (folder / "a.json").write_text(
        json.dumps({"sys": 110, "source": "telegram", "session": "session_a"}),
        encoding="utf-8",
    )
    (folder / "b.jpg").write_bytes(FAKE_JPEG)
    (folder / "b.json").write_text(
        json.dumps({"sys": 115, "source": "upload", "session": "session_b"}),
        encoding="utf-8",
    )

    await admin_client.post("/admin/ocr-training/samples/import")

    # List all
    res = await admin_client.get("/admin/ocr-training/samples")
    assert res.status_code == 200
    envelope = res.json()
    assert envelope["total"] == 2
    assert len(envelope["items"]) == 2

    sample_a_id = next(s["id"] for s in envelope["items"] if s["source"] == "telegram")

    # Filter source
    res_source = await admin_client.get("/admin/ocr-training/samples?source=telegram")
    assert res_source.status_code == 200
    assert res_source.json()["total"] == 1
    assert res_source.json()["items"][0]["source"] == "telegram"

    # Filter session
    res_sess = await admin_client.get("/admin/ocr-training/samples?session=session_b")
    assert res_sess.status_code == 200
    assert res_sess.json()["total"] == 1
    assert res_sess.json()["items"][0]["session"] == "session_b"

    # Filter labeled (currently neither is labeled)
    res_lab_true = await admin_client.get("/admin/ocr-training/samples?labeled=true")
    assert res_lab_true.status_code == 200
    assert res_lab_true.json()["total"] == 0

    res_lab_false = await admin_client.get("/admin/ocr-training/samples?labeled=false")
    assert res_lab_false.status_code == 200
    assert res_lab_false.json()["total"] == 2

    # Get by ID
    res_get = await admin_client.get(f"/admin/ocr-training/samples/{sample_a_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == sample_a_id

    # Get non-existent
    res_not_found = await admin_client.get(f"/admin/ocr-training/samples/{UUID(int=9999)}")
    assert res_not_found.status_code == 404


@pytest.mark.asyncio
async def test_labels_save_and_read_back(admin_client: AsyncClient, setup_ocr_dirs):
    photos_dir, _, _ = setup_ocr_dirs
    folder = photos_dir / "2024-07"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s1.jpg").write_bytes(FAKE_JPEG)
    (folder / "s1.json").write_text(json.dumps({"sys": 120}), encoding="utf-8")

    await admin_client.post("/admin/ocr-training/samples/import")
    list_res = await admin_client.get("/admin/ocr-training/samples")
    sample_id = list_res.json()["items"][0]["id"]

    display_box = {"cx": 0.5, "cy": 0.5, "w": 0.8, "h": 0.6}
    digit_boxes = [
        {"cls": 1, "cx": 0.2, "cy": 0.3, "w": 0.1, "h": 0.1},
        {"cls": 2, "cx": 0.3, "cy": 0.3, "w": 0.1, "h": 0.1},
    ]

    put_res = await admin_client.put(
        f"/admin/ocr-training/samples/{sample_id}/labels",
        json={"display_box": display_box, "digit_boxes": digit_boxes},
    )
    assert put_res.status_code == 200
    updated_sample = put_res.json()
    assert updated_sample["display_box"] == display_box
    assert updated_sample["digit_boxes"] == digit_boxes
    assert updated_sample["label_source"] == "manual"

    # Read back via GET
    get_res = await admin_client.get(f"/admin/ocr-training/samples/{sample_id}")
    assert get_res.status_code == 200
    assert get_res.json()["display_box"] == display_box
    assert get_res.json()["label_source"] == "manual"

    # Filter labeled=true now returns 1
    lab_true = await admin_client.get("/admin/ocr-training/samples?labeled=true")
    assert lab_true.json()["total"] == 1


@pytest.mark.asyncio
async def test_dataset_split_rule(admin_client: AsyncClient, setup_ocr_dirs, session: AsyncSession):
    photos_dir, _, _ = setup_ocr_dirs
    folder = photos_dir / "2024-08"
    folder.mkdir(parents=True, exist_ok=True)

    # 10 photos across sessions
    for i in range(10):
        stem = f"photo_{i}"
        (folder / f"{stem}.jpg").write_bytes(FAKE_JPEG)
        sess = "test_session_holdout" if i < 3 else f"session_grp_{i}"
        (folder / f"{stem}.json").write_text(
            json.dumps({"sys": 120 + i, "session": sess}),
            encoding="utf-8",
        )

    await admin_client.post("/admin/ocr-training/samples/import")

    # Create dataset with test_sessions specifying test_session_holdout and photo_9 stem
    ds_payload = {
        "name": "benchmark_v1",
        "test_sessions": ["test_session_holdout", "photo_9"],
    }
    ds_res = await admin_client.post("/admin/ocr-training/datasets", json=ds_payload)
    assert ds_res.status_code == 201
    ds_data = ds_res.json()
    assert ds_data["name"] == "benchmark_v1"
    assert ds_data["total_samples"] == 10
    # photo_0, photo_1, photo_2 are in test_session_holdout -> test
    # photo_9 has stem in test_sessions -> test
    # Total test count should be at least 4
    assert ds_data["test_samples"] >= 4

    # Duplicate name should return 409
    dup_res = await admin_client.post("/admin/ocr-training/datasets", json=ds_payload)
    assert dup_res.status_code == 409

    # GET /datasets
    list_ds = await admin_client.get("/admin/ocr-training/datasets")
    assert list_ds.status_code == 200
    assert len(list_ds.json()) == 1

    # GET /datasets/{id}
    get_ds = await admin_client.get(f"/admin/ocr-training/datasets/{ds_data['id']}")
    assert get_ds.status_code == 200
    assert get_ds.json()["total_samples"] == 10


@pytest.mark.asyncio
async def test_job_lifecycle_and_service_claim(
    admin_client: AsyncClient,
    trainer_client: AsyncClient,
    setup_ocr_dirs,
    session: AsyncSession,
):
    photos_dir, models_dir, _ = setup_ocr_dirs
    folder = photos_dir / "2024-09"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x1.jpg").write_bytes(FAKE_JPEG)
    (folder / "x1.json").write_text(
        json.dumps({"sys": 120, "dia": 80, "pul": 70}), encoding="utf-8"
    )

    await admin_client.post("/admin/ocr-training/samples/import")
    ds_res = await admin_client.post(
        "/admin/ocr-training/datasets",
        json={"name": "lifecycle_ds", "test_sessions": []},
    )
    dataset_id = ds_res.json()["id"]

    # When no job is queued, claim returns 204
    empty_claim = await trainer_client.post("/ocr-trainer/jobs/claim")
    assert empty_claim.status_code == 204

    # 1. Admin creates job
    job_res = await admin_client.post(
        "/admin/ocr-training/jobs",
        json={"dataset_id": dataset_id, "params": {"epochs": 10}},
    )
    assert job_res.status_code == 201
    job_id = job_res.json()["id"]
    assert job_res.json()["status"] == "queued"

    # 2. Service claims job
    claim_res = await trainer_client.post("/ocr-trainer/jobs/claim")
    assert claim_res.status_code == 200
    claimed_data = claim_res.json()
    assert claimed_data["job"]["id"] == job_id
    assert claimed_data["job"]["status"] == "running"
    assert claimed_data["job"]["claimed_at"] is not None
    assert len(claimed_data["manifest"]) == 1
    manifest_item = claimed_data["manifest"][0]
    assert manifest_item["ref_sys"] == 120
    assert manifest_item["photo_url"] == f"/ocr-trainer/samples/{manifest_item['sample_id']}/photo"

    # 3. Trainer downloads sample photo
    photo_res = await trainer_client.get(manifest_item["photo_url"])
    assert photo_res.status_code == 200
    assert photo_res.content == FAKE_JPEG

    # Also admin photo download works
    sample_endpoint = f"/admin/ocr-training/samples/{manifest_item['sample_id']}/photo"
    admin_photo = await admin_client.get(sample_endpoint)
    assert admin_photo.status_code == 200
    assert admin_photo.content == FAKE_JPEG

    # 4. Service submits job result
    report_content = json.dumps({"version": "v1.0.0", "acc": 0.99, "groups": {"test": 0.98}})
    files = {
        "display_model": ("display.onnx", io.BytesIO(FAKE_ONNX), "application/octet-stream"),
        "digit_model": ("digit.onnx", io.BytesIO(FAKE_ONNX), "application/octet-stream"),
    }
    data = {"report": report_content}

    result_res = await trainer_client.post(
        f"/ocr-trainer/jobs/{job_id}/result",
        data=data,
        files=files,
    )
    assert result_res.status_code == 200
    model_data = result_res.json()
    assert model_data["version"] == "v1.0.0"
    assert model_data["is_current"] is False
    assert model_data["display_model_path"] == "v1.0.0/display_model.onnx"
    assert model_data["digit_model_path"] == "v1.0.0/digit_model.onnx"

    # Verify model files written to disk
    assert (models_dir / "v1.0.0" / "display_model.onnx").read_bytes() == FAKE_ONNX
    assert (models_dir / "v1.0.0" / "digit_model.onnx").read_bytes() == FAKE_ONNX

    # Check job succeeded
    get_job_res = await admin_client.get(f"/admin/ocr-training/jobs/{job_id}")
    assert get_job_res.status_code == 200
    assert get_job_res.json()["status"] == "succeeded"
    assert get_job_res.json()["model_id"] == model_data["id"]

    # Subsequent result submission returns 409 because job is no longer running
    dup_result = await trainer_client.post(
        f"/ocr-trainer/jobs/{job_id}/result",
        data=data,
        files={
            "display_model": ("d.onnx", io.BytesIO(FAKE_ONNX), "application/octet-stream"),
            "digit_model": ("d.onnx", io.BytesIO(FAKE_ONNX), "application/octet-stream"),
        },
    )
    assert dup_result.status_code == 409


@pytest.mark.asyncio
async def test_job_fail_path(
    admin_client: AsyncClient,
    trainer_client: AsyncClient,
    setup_ocr_dirs,
):
    photos_dir, _, _ = setup_ocr_dirs
    folder = photos_dir / "2024-10"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "y.jpg").write_bytes(FAKE_JPEG)
    (folder / "y.json").write_text("{}", encoding="utf-8")

    await admin_client.post("/admin/ocr-training/samples/import")
    ds_res = await admin_client.post(
        "/admin/ocr-training/datasets",
        json={"name": "fail_ds", "test_sessions": []},
    )
    dataset_id = ds_res.json()["id"]

    job_res = await admin_client.post(
        "/admin/ocr-training/jobs",
        json={"dataset_id": dataset_id, "params": {}},
    )
    job_id = job_res.json()["id"]

    # Attempt to fail before claim (queued state) -> 409
    early_fail = await trainer_client.post(
        f"/ocr-trainer/jobs/{job_id}/fail",
        json={"error": "Premature fail"},
    )
    assert early_fail.status_code == 409

    # Claim job -> running
    await trainer_client.post("/ocr-trainer/jobs/claim")

    # Fail job
    fail_res = await trainer_client.post(
        f"/ocr-trainer/jobs/{job_id}/fail",
        json={"error": "GPU out of memory"},
    )
    assert fail_res.status_code == 200
    assert fail_res.json()["status"] == "failed"
    assert fail_res.json()["error"] == "GPU out of memory"

    # Attempt to fail again -> 409
    dup_fail = await trainer_client.post(
        f"/ocr-trainer/jobs/{job_id}/fail",
        json={"error": "Again"},
    )
    assert dup_fail.status_code == 409


@pytest.mark.asyncio
async def test_service_token_auth_verification(session: AsyncSession, setup_ocr_dirs):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)

    # 1. Missing Authorization header -> 401
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.post("/ocr-trainer/jobs/claim")
        assert res.status_code == 401

    # 2. Wrong token -> 401
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer wrong-token"},
    ) as c:
        res = await c.post("/ocr-trainer/jobs/claim")
        assert res.status_code == 401

    # 3. Malformed header -> 401
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Basic 1234"},
    ) as c:
        res = await c.post("/ocr-trainer/jobs/claim")
        assert res.status_code == 401

    # 4. When ocr_trainer_token is unset in settings -> 401
    no_token_settings = get_settings().model_copy(update={"ocr_trainer_token": None})
    app.dependency_overrides[get_settings] = lambda: no_token_settings
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer any-token"},
    ) as c:
        res = await c.post("/ocr-trainer/jobs/claim")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_normal_user_forbidden_on_admin_endpoints(
    user_client: AsyncClient,
):
    # A normal user gets 403 on admin endpoints
    res1 = await user_client.post("/admin/ocr-training/samples/import")
    assert res1.status_code == 403

    res2 = await user_client.get("/admin/ocr-training/samples")
    assert res2.status_code == 403

    res3 = await user_client.get("/admin/ocr-training/datasets")
    assert res3.status_code == 403

    res4 = await user_client.post("/admin/ocr-training/jobs", json={"dataset_id": str(UUID(int=1))})
    assert res4.status_code == 403

    res5 = await user_client.get("/admin/ocr-training/models")
    assert res5.status_code == 403


@pytest.mark.asyncio
async def test_photo_path_traversal_rejected(
    admin_client: AsyncClient,
    setup_ocr_dirs,
    session: AsyncSession,
):
    # Directly insert sample with path traversal in DB
    malicious_sample = OcrSample(
        photo_path="../../etc/passwd",
        ref_sys=120,
    )
    session.add(malicious_sample)
    await session.commit()
    await session.refresh(malicious_sample)

    res = await admin_client.get(f"/admin/ocr-training/samples/{malicious_sample.id}/photo")
    assert res.status_code == 400
    assert "traversal" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_make_current_leaves_exactly_one_current_model(
    admin_client: AsyncClient,
    session: AsyncSession,
):
    # Insert dataset, two jobs and two models directly
    ds = OcrDataset(name="ds_models_test", test_sessions=[])
    session.add(ds)
    await session.flush()
    assert ds.id is not None

    j1 = OcrTrainingJob(dataset_id=ds.id, status="succeeded")
    j2 = OcrTrainingJob(dataset_id=ds.id, status="succeeded")
    session.add(j1)
    session.add(j2)
    await session.flush()

    m1 = OcrModel(
        version="v1",
        job_id=j1.id,
        display_model_path="v1/disp.onnx",
        digit_model_path="v1/dig.onnx",
        report={},
        is_current=False,
    )
    m2 = OcrModel(
        version="v2",
        job_id=j2.id,
        display_model_path="v2/disp.onnx",
        digit_model_path="v2/dig.onnx",
        report={},
        is_current=False,
    )
    session.add(m1)
    session.add(m2)
    await session.commit()

    # 1. Make m1 current
    res1 = await admin_client.post(f"/admin/ocr-training/models/{m1.id}/make-current")
    assert res1.status_code == 200
    assert res1.json()["is_current"] is True

    await session.refresh(m1)
    await session.refresh(m2)
    assert m1.is_current is True
    assert m2.is_current is False

    # 2. Make m2 current
    res2 = await admin_client.post(f"/admin/ocr-training/models/{m2.id}/make-current")
    assert res2.status_code == 200
    assert res2.json()["is_current"] is True

    await session.refresh(m1)
    await session.refresh(m2)
    assert m1.is_current is False
    assert m2.is_current is True

    # Exactly one model has is_current = True
    stmt = select(OcrModel).where(OcrModel.is_current.is_(True))
    current_models = (await session.exec(stmt)).all()
    assert len(current_models) == 1
    assert current_models[0].id == m2.id
