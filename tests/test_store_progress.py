import json
import shutil
from pathlib import Path

from store import TaskStore


def test_public_view_uses_latest_retain_pipeline_event_for_stage_text() -> None:
    test_root = Path(__file__).resolve().parents[1] / ".store_progress_test"
    shutil.rmtree(test_root, ignore_errors=True)
    work_dir = test_root / "task"
    logs_dir = work_dir / "retain_work" / "logs"
    logs_dir.mkdir(parents=True)
    events_path = logs_dir / "pipeline_events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stage": "translating",
                        "user_stage": "translation",
                        "substage": "translation_batches",
                        "message": "开始批量翻译",
                        "progress_current": 0,
                        "progress_total": 4,
                        "progress_unit": "batch",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "stage": "rendering",
                        "user_stage": "render",
                        "substage": "render_pages",
                        "message": "正在渲染页面",
                        "progress_current": 2,
                        "progress_total": 4,
                        "progress_unit": "page",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    store = TaskStore()
    store.add(
        {
            "task_id": "task-1",
            "status": "running",
            "work_dir": str(work_dir),
            "total_pages": 4,
            "translated_pages": 1,
            "inputs": {},
        }
    )

    view = store.public_view("task-1")

    assert view["stage_text"] == "正在渲染页面: 2 / 4 页"
    assert view["pipeline_stage"] == "rendering"
    assert view["pipeline_user_stage"] == "render"
    assert view["pipeline_progress_current"] == 2
    assert view["pipeline_progress_total"] == 4
    assert view["pipeline_progress_percent"] == 50.0

    shutil.rmtree(test_root, ignore_errors=True)
