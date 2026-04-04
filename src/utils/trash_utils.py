from __future__ import annotations

import os


_FILE_IN_USE_WINERRORS = {
    32,
    -2144927705,  # Windows shell OLE error 0x80270027 from send2trash
}


def build_trash_failure_message(path: str, error: BaseException) -> str:
    """Return a user-facing message for a failed move-to-trash operation."""
    filename = os.path.basename(path) or path
    reason = _describe_trash_failure_reason(error)
    detail = _extract_error_detail(error)

    lines = [
        f"「{filename}」を削除できませんでした。",
        "",
        reason,
    ]
    if detail and detail != reason:
        lines.extend(["", f"詳細: {detail}"])
    return "\n".join(lines)


def _describe_trash_failure_reason(error: BaseException) -> str:
    winerror = getattr(error, "winerror", None)
    errno = getattr(error, "errno", None)
    text = _extract_error_detail(error).lower()

    if (
        winerror in _FILE_IN_USE_WINERRORS
        or errno == 32
        or "0x80270027" in text
        or "another process" in text
        or "being used by another process" in text
    ):
        return "他のアプリでこのファイルを使用中のため、削除できません。開いているアプリを閉じてから再度お試しください。"

    if isinstance(error, PermissionError) or winerror == 5:
        return "このファイルを削除する権限がないため、削除できません。"

    if isinstance(error, FileNotFoundError):
        return "削除対象のファイルが見つかりませんでした。"

    return "ファイルをゴミ箱へ移動できませんでした。"


def _extract_error_detail(error: BaseException) -> str:
    detail = getattr(error, "strerror", None)
    if detail:
        return str(detail)
    return str(error)
