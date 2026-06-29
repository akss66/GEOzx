"""Agent system prompt 装载器。

prompt 以 Markdown 存于 `app/prompts/<name>.md`，按名读取并缓存。
v1 草稿 prompt 由本项目起草，权威版本待 `配置表.xlsx` 校准（见各 .md 顶部 TODO）。
"""

from functools import cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """读取 `prompts/<name>.md` 内容（缓存）。文件缺失抛 FileNotFoundError。"""
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"未找到 Agent prompt：{path}")
    return path.read_text(encoding="utf-8").strip()


def available_prompts() -> list[str]:
    """列出已存在的 prompt 名（不含扩展名）。"""
    if not _PROMPT_DIR.exists():
        return []
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.md"))
