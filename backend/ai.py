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


# ── Research & Cite ───────────────────────────────────────────────────────────

_RESEARCH_SYSTEM = """You are an expert policy debate researcher. Given a project name, debate topic, and argument description:

1. Write a "link story": a concise 2-4 sentence narrative that chains the argument from its root cause to its terminal impact. Each sentence should state one causal link.

2. Generate 8 specific article/source suggestions — one per causal link or key warrant — that would provide evidence for this argument. Focus on real, citable academic papers, government reports, and credible journalism from your training knowledge.

Return valid JSON only (no markdown fences):
{
  "link_story": "...",
  "articles": [
    {
      "tag": "one- or two-sentence debate tag stating what the card proves",
      "author": "Last, First",
      "author_qualifications": "professional role and institution",
      "date": "YYYY",
      "title": "full article or paper title",
      "publisher": "journal, website, or publishing institution",
      "url": "",
      "initials": "FL",
      "excerpt": "2-3 sentence passage that forms the body of the card"
    }
  ]
}"""

_CITE_SYSTEM = """You are a debate cite formatter. Given article text or metadata, extract the citation information.

Return valid JSON only (no markdown fences):
{
  "author": "Last, First  (if multiple authors use 'Last et al.')",
  "author_qualifications": "professional role, title, and institution",
  "date": "YYYY  or  YYYY/MM  or  YYYY/MM/DD",
  "title": "article or paper title",
  "publisher": "journal, website, or publication name",
  "url": "URL if present in the text, else empty string",
  "initials": "cite initials — first letter of first name + first letter of last name, e.g. 'JD'"
}"""


_QUERY_SYSTEM = (
    "Generate 3 specific web search queries to find academic or policy articles that support "
    "the given debate argument. Return a JSON array of strings only — no other text:\n"
    '["query one", "query two", "query three"]'
)

_RESEARCH_WITH_SOURCES_SYSTEM = """You are an expert policy debate researcher. Given a project name, topic, argument description, and REAL web search results:

1. Write a "link story": a concise 2-4 sentence narrative that chains the argument from root cause to terminal impact.

2. Generate 8 article suggestions grounded in the provided search results. Prefer sources that appear in the search results — use their exact titles and URLs. You may add 1-2 additional suggestions from your training knowledge only if the search results leave key links in the argument unsupported; leave their URL field empty.

Return valid JSON only (no markdown fences):
{
  "link_story": "...",
  "articles": [
    {
      "tag": "one- or two-sentence debate tag stating what the card proves",
      "author": "Last, First",
      "author_qualifications": "professional role and institution",
      "date": "YYYY",
      "title": "exact title from search results or training knowledge",
      "publisher": "journal, website, or publishing institution",
      "url": "exact URL from search results, or empty string if not from search",
      "initials": "FL",
      "excerpt": "2-3 sentence passage that forms the body of the card"
    }
  ]
}"""


async def _generate_search_queries(topic: str, description: str) -> list[str]:
    user_msg = f"TOPIC: {topic}\nARGUMENT: {description}"
    try:
        text, _, _ = await router.call(_QUERY_SYSTEM, user_msg, max_tokens=200)
        arr_match = re.search(r"\[[\s\S]*\]", text)
        if arr_match:
            queries = json.loads(arr_match.group(0))
            return [str(q) for q in queries[:4] if q]
    except Exception:
        pass
    return [topic] if topic else []


async def research_project(
    project_name: str, topic: str, description: str,
    search_results: list[dict] | None = None,
) -> tuple[dict, str, dict]:
    """Returns (result_dict, model_id, tokens_dict).

    If search_results is provided (from Brave Search), they are injected into the
    prompt so the AI returns real URLs instead of hallucinated ones.
    """
    if search_results:
        results_text = "\n".join(
            f"{i+1}. [{r['title']}]({r['url']})\n   {r['snippet']}"
            for i, r in enumerate(search_results)
        )
        user_msg = (
            f"PROJECT NAME: {project_name}\n"
            f"TOPIC: {topic}\n"
            f"ARGUMENT DESCRIPTION: {description}\n\n"
            f"SEARCH RESULTS:\n{results_text}"
        )
        system = _RESEARCH_WITH_SOURCES_SYSTEM
    else:
        user_msg = (
            f"PROJECT NAME: {project_name}\n"
            f"TOPIC: {topic}\n"
            f"ARGUMENT DESCRIPTION: {description}"
        )
        system = _RESEARCH_SYSTEM

    try:
        text, model_id, tokens = await router.call(system, user_msg, max_tokens=4096)
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json.loads(json_match.group(0)), model_id, tokens
        return {"link_story": "", "articles": []}, model_id, tokens
    except json.JSONDecodeError:
        return {"link_story": "", "articles": []}, "none", _EMPTY_TOKENS
    except Exception as exc:
        return {"link_story": "", "articles": [], "error": str(exc)}, "none", _EMPTY_TOKENS


async def generate_cite(article_text: str) -> tuple[dict, str, dict]:
    """Returns (cite_dict, model_id, tokens_dict)."""
    try:
        text, model_id, tokens = await router.call(
            _CITE_SYSTEM, article_text[:4000], max_tokens=512
        )
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json.loads(json_match.group(0)), model_id, tokens
        return {}, model_id, tokens
    except json.JSONDecodeError:
        return {}, "none", _EMPTY_TOKENS
    except Exception as exc:
        return {"error": str(exc)}, "none", _EMPTY_TOKENS


async def cut_card_with_context(
    card: dict,
    project_name: str,
    link_story: str,
    topic: str,
    underline_prompt: str,
    highlight_prompt: str,
) -> tuple[dict, dict, str, dict, dict]:
    """Returns (underline_result, highlight_result, model_id, ul_tokens, hl_tokens)."""
    context_prefix = (
        f"PROJECT: {project_name}\n"
        f"ARGUMENT CHAIN: {link_story}\n"
        f"TOPIC: {topic}\n\n"
    )
    ul_user_msg = (
        context_prefix
        + f"CARD TAG: {card.get('tag', '')}\n"
        f"CITATION: {card.get('author', '')} {card.get('date', '')}, "
        f"\"{card.get('title', '')}\"\n"
        f"BODY:\n{(card.get('card_text') or '')[:3000]}"
    )

    try:
        text, model_id, ul_tokens = await router.call(
            underline_prompt, ul_user_msg, max_tokens=2048
        )
        json_match = re.search(r"\{[\s\S]*\}", text)
        underline_result = (
            json.loads(json_match.group(0))
            if json_match
            else {"relevant": False, "reason": "No JSON", "underlined": []}
        )
    except Exception as exc:
        return (
            {"relevant": False, "reason": str(exc), "underlined": []},
            {"highlighted": []},
            "none",
            _EMPTY_TOKENS,
            _EMPTY_TOKENS,
        )

    underlined = underline_result.get("underlined", [])
    if not underlined:
        return underline_result, {"highlighted": []}, model_id, ul_tokens, _EMPTY_TOKENS

    numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(underlined))
    hl_user_msg = f"CARD TAG: {card.get('tag', '')}\n\nUNDERLINED PASSAGES:\n{numbered}"

    try:
        text, _, hl_tokens = await router.call(
            highlight_prompt, hl_user_msg, max_tokens=1024
        )
        json_match = re.search(r"\{[\s\S]*\}", text)
        highlight_result = (
            json.loads(json_match.group(0)) if json_match else {"highlighted": []}
        )
    except Exception:
        highlight_result = {"highlighted": []}
        hl_tokens = _EMPTY_TOKENS

    return underline_result, highlight_result, model_id, ul_tokens, hl_tokens
