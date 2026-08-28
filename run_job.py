"""子进程入口:读取 spec JSON 路径,调用官方 provider_pipeline.main()。

由 app.py 通过 subprocess.run 拉起,每个请求一个独立进程,
天然隔离 apply_layout_tuning 的进程全局态与凭证环境变量。

用法:
    python run_job.py <path-to-spec.json>
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# 把 pipeline/ 加入 sys.path,使其内部 `from services... / from foundation... / from runtime...` 可解析
_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from services.ocr_provider.provider_pipeline import main  # noqa: E402


def run() -> None:
    if len(sys.argv) < 2:
        print("usage: python run_job.py <spec.json>", file=sys.stderr)
        sys.exit(2)
    spec_path = sys.argv[1]
    # provider_pipeline.main() 内部用 argparse 解析 --spec,这里把 argv 对齐
    sys.argv = ["run_job", "--spec", spec_path]
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        # 管道跑完即强制退出:pipeline 里遗留的非 daemon 线程(线程池/HTTP 连接池等)
        # 会让解释器在 main() 返回后仍不退,导致父进程 subprocess.run(capture_output=True)
        # 一直阻塞到 retain_timeout(任务卡在 running)。产物在 main() 内已全部落盘,
        # 无需走正常解释器清理,直接 os._exit 立即终止(先 flush 保证日志/事件流完整写盘)。
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    run()
