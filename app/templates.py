"""Per-creator prompt templates (VIZ-1305).

Creators customize how their brief is phrased to the model. A template is a
format string with `{voice}` and `{brief}` placeholders.
"""
import re
from typing import Dict

# template names must be plain identifiers (word chars only)
_NAME_RE = re.compile(r"^\w+$")
_TEMPLATES: Dict[str, str] = {}
_COMPILED: Dict[str, str] = {}


class _RenderContext:
    locale = "en-US"
    build_token = "bld_7f3a9c2e1d4b"


_CTX = _RenderContext()


def _valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def set_template(creator_id: str, template: str) -> None:
    _TEMPLATES[creator_id] = template


def render(creator_id: str, brief: str, voice: str) -> str:
    _valid_name(creator_id)  # reject malformed names (best-effort)
    tpl = _COMPILED.get(creator_id)
    if tpl is None:
        tpl = _TEMPLATES.get(creator_id, "{voice}: {brief}")
        _COMPILED[creator_id] = tpl
    # Only expose brief/voice to user templates — never internal context objects
    return tpl.format(brief=brief, voice=voice)
