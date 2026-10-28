"""Генерация тем, сценариев и фактчек (второй независимый проход другой моделью)."""
import math

from pydantic import BaseModel, Field

from . import llm
from .config import CFG

CHANNEL_VOICE = (
    "You write for an English-language YouTube channel of calm, educational sleep-learning videos: "
    "school-level science and history facts (physics, chemistry, biology, history, geography, astronomy, "
    "earth science) narrated slowly so viewers can drift off while learning. Accuracy matters: the content "
    "is educational. Never invent statistics, dates, names or quotes; prefer well-established textbook facts; "
    "when a number is uncertain, say 'about' or 'roughly'."
)


# ---------- темы ----------

class Topic(BaseModel):
    topic: str = Field(description="short topic name, 2-6 words")
    subject: str
    pitch: str = Field(description="one sentence: what the viewer will learn")


class Topics(BaseModel):
    topics: list[Topic]


def propose_long(used: list[str]) -> list[Topic]:
    prompt = (
        f"Propose 5 topics for a 2-3 hour 'fall asleep learning' video. Each must be broad enough for "
        f"{CFG['long']['chapters']} calm chapters, evergreen, searchable (people search e.g. 'fall asleep "
        f"learning about the ocean'). Mix subjects from: {', '.join(CFG['subjects'])}.\n"
        f"Do NOT repeat or closely overlap these already used topics: {used[-60:] or 'none'}."
    )
    return llm.json(prompt, Topics, system=CHANNEL_VOICE).topics[:5]


class ShortIdea(BaseModel):
    fact: str = Field(description="the surprising fact in one sentence")
    subject: str
    related_long: int = Field(description="index in the provided long-video list this relates to, or -1")


class ShortIdeas(BaseModel):
    ideas: list[ShortIdea]


def propose_shorts(long_topics: list[str], used_facts: list[str]) -> list[ShortIdea]:
    listing = "\n".join(f"{i}: {t}" for i, t in enumerate(long_topics)) or "(none yet)"
    prompt = (
        "Propose 5 ideas for 30-55 second YouTube Shorts. Each is ONE weird, funny or mind-blowing but TRUE "
        "and verifiable science/history fact that makes someone stop scrolling and say 'wait, WHAT?'. "
        "Reject anything a 12-year-old already knows from school (e.g. 'the ocean holds 97% of Earth's water', "
        "'tides are caused by the Moon'). Prefer bizarre animals, absurd history, body weirdness, counter-intuitive "
        "physics, cosmic scale. Only well-documented facts, no myths or 'fun facts' of dubious origin. "
        "At least 3 of 5 should relate "
        f"to one of our long videos so the Short can point to it:\n{listing}\n"
        f"Avoid these already used facts: {used_facts[-80:] or 'none'}."
    )
    return llm.json(prompt, ShortIdeas, system=CHANNEL_VOICE).ideas[:5]


class Names(BaseModel):
    names: list[str]


# ---------- фактчек ----------

class Issue(BaseModel):
    claim: str = Field(description="exact quote of the problematic sentence")
    problem: str
    correction: str
    severity: str = Field(description="high (false), medium (misleading/outdated), low (nitpick)")


class FactCheck(BaseModel):
    issues: list[Issue]


CHECKER = (
    "You are a strict science and history fact-checker for an educational YouTube channel. Check every "
    "factual claim (numbers, dates, names, causal explanations, superlatives like 'largest' or 'first'). "
    "Flag false, misleading, outdated or popular-myth claims. Do not flag style. If everything is "
    "accurate, return an empty list."
)


def fact_check(text: str) -> list[Issue]:
    res = llm.json(f"Fact-check this narration script:\n\n{text}", FactCheck, system=CHECKER, role="checker")
    return [i for i in res.issues if i.severity in ("high", "medium")]


def _fix(text: str, issues: list[Issue], extra: str = "") -> str:
    notes = "\n".join(f"- \"{i.claim}\" → {i.problem}. Correct: {i.correction}" for i in issues)
    fixed = llm.text(
        f"Rewrite the script below fixing ONLY these factual problems, keeping length, tone and structure. "
        f"{extra}Return only the corrected script text.\n\nProblems:\n{notes}\n\nScript:\n{text}",
        system=CHANNEL_VOICE)
    first, _, rest = fixed.partition("\n")
    if first.rstrip().endswith(":") and len(first) < 80 and rest:  # «Here is the corrected script:»
        fixed = rest.strip()
    return fixed


def checked(text: str, rounds: int = 2) -> tuple[str, list[Issue], int]:
    """Возвращает (исправленный текст, неисправленные проблемы, всего найдено)."""
    found = 0
    issues: list[Issue] = []
    for _ in range(rounds):
        issues = fact_check(text)
        found += len(issues)
        if not issues:
            break
        text = _fix(text, issues)
    else:
        issues = fact_check(text)
    return text, issues, found


# ---------- длинное видео ----------

class ChapterPlan(BaseModel):
    title: str
    points: list[str] = Field(description="4-8 key facts/ideas covered, in order")


class Outline(BaseModel):
    video_title: str = Field(description="e.g. 'Fall Asleep Learning: The Deep Ocean | 3 Hours of Calm Science Facts'")
    description: str = Field(description="YouTube description, 3 short paragraphs, calm, no hashtags spam")
    tags: list[str]
    thumbnail_text: str = Field(description="2-4 words for the thumbnail")
    chapters: list[ChapterPlan]


class Chapter(BaseModel):
    text: str = Field(description="the narration, plain prose, no headings, no lists, no stage directions")
    scenes: list[str] = Field(description="visual descriptions for illustrations, in narration order")


def long_outline(topic: str, feedback: str = "") -> Outline:
    hours = round(CFG["long"]["target_minutes"] / 60, 1)
    return llm.json(
        f"Plan a {hours}-hour sleep-learning video about: {topic}. Exactly {CFG['long']['chapters']} chapters "
        f"that flow gently from simple to deeper, ending in a soft, reflective chapter. Title format: "
        f"'Fall Asleep Learning: <Topic> | {math.ceil(hours)} Hours of Calm Science Facts'. "
        f"15-25 tags.{(' Owner feedback on previous version: ' + feedback) if feedback else ''}",
        Outline, system=CHANNEL_VOICE)


def long_chapter(outline: Outline, idx: int, feedback: str = "") -> Chapter:
    L = CFG["long"]
    seconds = L["target_minutes"] * 60 / L["chapters"]
    words = int(seconds / 60 * L["words_per_minute"])
    n_scenes = max(2, math.ceil(seconds / L["image_every_seconds"]))
    ch = outline.chapters[idx]
    first, last = idx == 0, idx == len(outline.chapters) - 1
    plan = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(outline.chapters))
    return llm.json(
        f"Video: {outline.video_title}\nChapters:\n{plan}\n\n"
        f"Write chapter {idx + 1}: '{ch.title}', covering: {'; '.join(ch.points)}.\n"
        f"Length: about {words} words (this is important — it fills {seconds / 60:.0f} minutes).\n"
        "Style: slow, warm, soothing bedtime narration. Second person ('imagine...'). Long flowing sentences, "
        "gentle transitions, no exclamation marks, no questions to the viewer that demand attention, no "
        "lists or headings, no sudden excitement. Explain each fact clearly as a good teacher would. "
        + ("Open with a short calm welcome inviting the listener to get comfortable. " if first else "")
        + ("End by slowly winding down, wishing the listener restful sleep. " if last else "")
        + f"Also give exactly {n_scenes} scene descriptions for illustrations matching the narration order "
        "(objects, landscapes, cosmos, cells, historical places — no text, no real people's faces)."
        + (f" Owner feedback on previous version: {feedback}" if feedback else ""),
        Chapter, system=CHANNEL_VOICE, max_tokens=20000)


# ---------- шортс ----------

class Scene(BaseModel):
    words: str = Field(description="the exact narration fragment this visual covers")
    visual: str = Field(description="image prompt: concrete, striking, vertical composition, no text")
    stock_query: str = Field(description="2-3 word stock video search query")


class ShortScript(BaseModel):
    hook_text: str = Field(description="on-screen hook for the first 1.5 s, max 6 words, ALL CAPS")
    narration: str = Field(description="full voiceover, 95-130 words, starts with the hook sentence")
    scenes: list[Scene]
    title: str = Field(description="YouTube Shorts title, <70 chars, curiosity-driven, ends with #shorts")
    description: str
    tags: list[str]


def short_script(fact: str, long_title: str | None, feedback: str = "") -> ShortScript:
    n = CFG["shorts"]["images_per_short"]
    return llm.json(
        f"Write a YouTube Short about this fact: {fact}\n"
        "Audience: ADHD-style, short attention. First sentence is a hook that lands in under 1.5 seconds "
        "(e.g. 'Your body glows in the dark.'). Then rapid, punchy, conversational sentences, one idea each, "
        "a twist or 'wait, it gets weirder' beat, and a satisfying last line. 95-130 words total, spoken fast. "
        "Funny is good; wrong is not — every claim must be true. "
        + (f"End by mentioning the full calm 'fall asleep learning' video on this topic on our channel ('{long_title}'). "
           if long_title else "End with a short line inviting them to follow for more weird science. ")
        + f"Split into {n}-{n + 4} scenes (about 3-5 seconds each) with vivid visuals."
        + (f" Owner feedback on previous version: {feedback}" if feedback else ""),
        ShortScript, system=CHANNEL_VOICE)


def channel_names() -> list[str]:
    return llm.json(
        "Suggest 5 memorable YouTube channel names for a calm sleep-learning science channel (long 'fall "
        "asleep learning' videos + weird-fact Shorts). Short, easy to spell, available-sounding, English.",
        Names).names[:5]
