"""PC活動の収集。集計ロジックは ActivityWarch 側にあり、ここではそのJSON出力を受け取るだけ。"""

import json
from pathlib import Path
import subprocess


class CollectError(RuntimeError):
    """収集元から読み取れなかった。既存の公開データは消さずに中断する。"""


def collect(days, project_dir):
    """指定日の集計を {日付文字列: 辞書} で返す。"""
    if not days:
        return {}
    project_dir = Path(project_dir)
    python = project_dir / ".venv" / "Scripts" / "python.exe"
    script = project_dir / "activitywatch_to_json.py"
    if not python.exists() or not script.exists():
        raise CollectError(f"ActivityWarch の実行環境が見つかりません：{project_dir}")
    command = [str(python), "-X", "utf8", str(script), "--date", *[day.isoformat() for day in days]]
    try:
        result = subprocess.run(command, cwd=project_dir, capture_output=True, text=True,
                                encoding="utf-8", timeout=900)
    except subprocess.TimeoutExpired as error:
        raise CollectError("ActivityWatch の集計が時間内に終わりませんでした") from error
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        reason = lines[-1] if lines else "原因不明"
        raise CollectError(f"ActivityWatch の集計に失敗しました（起動しているか確認）：{reason}")
    payload = json.loads(result.stdout)
    return {day["date"]: day for day in payload["days"]}
