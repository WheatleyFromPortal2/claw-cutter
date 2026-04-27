import json
import re
from pathlib import Path

from model_router import router

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_prompt_mtime: dict = {}
_prompt_cache: dict = {}


def get_prompts() -> dict:
    """Return prompts dict, reloading from disk if either file has changed."""
    global _prompt_mtime, _prompt_cache
    try:
        ul_file = _PROMPTS_DIR / "underline.txt"
        hl_file = _PROMPTS_DIR / "highlight.txt"
        ul_mtime = ul_file.stat().st_mtime
        hl_mtime = hl_file.stat().st_mtime
        if ul_mtime != _prompt_mtime.get("underline") or hl_mtime != _prompt_mtime.get("highlight"):
            _prompt_cache = {
                "underline": ul_file.read_text(),
                "highlight": hl_file.read_text(),
            }
            _prompt_mtime = {"underline": ul_mtime, "highlight": hl_mtime}
    except Exception as exc:
        if not _prompt_cache:
            raise RuntimeError(f"Failed to load prompts: {exc}")
    return _prompt_cache


def parse_cards(text: str) -> list:
    lines = text.split("\n")
    n = len(lines)

    def is_citation(s: str) -> bool:
        return (
            bool(re.search(r"\d{2,4}", s))
            and bool(re.search(r"[,;]", s))
            and (
                bool(re.search(r"[A-Z][a-z]", s))
                or bool(
                    re.search(
                        r"(?:University|Institute|Department|Journal|et al)", s, re.I
                    )
                )
            )
        )

    def is_tag(i: int) -> bool:
        s = lines[i].strip()
        if not s or len(s) >= 350:
            return False
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        return j < n and is_citation(lines[j].strip())

    cards = []
    i = 0
    while i < n:
        if is_tag(i):
            tag = lines[i].strip()
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            cite = lines[j].strip()
            body_lines = []
            k = j + 1
            while k < n:
                if is_tag(k):
                    break
                body_lines.append(lines[k])
                k += 1
            body = "\n".join(body_lines).strip()
            if len(body) > 80:
                cards.append({"tag": tag, "cite": cite, "body": body})
            i = k
        else:
            i += 1

    return cards


_EMPTY_TOKENS = {"input": 0, "output": 0}


async def underline_card(card: dict, topic: str, prompt: str) -> tuple[dict, str, dict]:
    """Returns (result_dict, model_id_used, tokens_dict)."""
    user_msg = (
        f"TOPIC: {topic}\n\n"
        f"CARD TAG: {card['tag']}\n"
        f"CITATION: {card['cite']}\n"
        f"BODY:\n{card['body'][:3000]}"
    )
    try:
        text, model_id, tokens = await router.call(prompt, user_msg, max_tokens=2048)
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json.loads(json_match.group(0)), model_id, tokens
        return {"relevant": False, "reason": "No JSON in response", "underlined": []}, model_id, tokens
    except json.JSONDecodeError:
        return {"relevant": False, "reason": "JSON parse error", "underlined": []}, "none", _EMPTY_TOKENS
    except Exception as exc:
        return {"relevant": False, "reason": str(exc), "underlined": []}, "none", _EMPTY_TOKENS


async def highlight_card(card: dict, underlined: list, prompt: str) -> tuple[dict, str, dict]:
    """Returns (result_dict, model_id_used, tokens_dict)."""
    numbered = "\n".join(f"{i + 1}. {phrase}" for i, phrase in enumerate(underlined))
    user_msg = f"CARD TAG: {card['tag']}\n\nUNDERLINED PASSAGES:\n{numbered}"
    try:
        text, model_id, tokens = await router.call(prompt, user_msg, max_tokens=1024)
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json.loads(json_match.group(0)), model_id, tokens
        return {"highlighted": []}, model_id, tokens
    except json.JSONDecodeError:
        return {"highlighted": []}, "none", _EMPTY_TOKENS
    except Exception:
        return {"highlighted": []}, "none", _EMPTY_TOKENS
