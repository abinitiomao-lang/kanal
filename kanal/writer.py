"""Генерация тем, сценариев и фактчек (второй независимый проход другой моделью)."""
import math
import random

import yaml

from pydantic import BaseModel, Field

from . import llm
from .config import CFG, ROOT

CHANNEL_VOICE = (
    "You write for an English-language YouTube channel of calm, educational sleep-learning videos: "
    "school-level science and history facts (physics, chemistry, biology, history, geography, astronomy, "
    "earth science, psychology) narrated slowly so viewers can drift off while learning. Accuracy matters: the content "
    "is educational. Never invent statistics, dates, names or quotes; prefer well-established textbook facts; "
    "when a number is uncertain, say 'about' or 'roughly'. "
    "When a topic is based on a book, credit the author by name, present models and methods as their ideas "
    "rather than settled science, and say plainly when later research failed to confirm a famous finding. "
    "Retell books in your own words with your own examples; never reproduce their text or long quotes."
)


# ---------- темы ----------

class Topic(BaseModel):
    topic: str = Field(description="short topic name, 2-6 words")
    subject: str
    pitch: str = Field(description="one sentence: what the viewer will learn")
    source: str = Field(default="", description="'<book> by <author>' if based on a book idea, else empty")


class Topics(BaseModel):
    topics: list[Topic]


def load_series() -> list[dict]:
    with open(ROOT / "assets" / "book_series.yaml") as f:
        return yaml.safe_load(f)


def next_book_parts(done: set[tuple[str, int]], limit: int = 10) -> list[dict]:
    """Следующая не снятая часть каждой книги (по порядку частей), не больше limit книг."""
    out = []
    for b in load_series():
        for n, part in enumerate(b["parts"], 1):
            if (b["id"], n) not in done:
                total = len(b["parts"])
                out.append({"topic": f"{b['book']}, Part {n} of {total}: {part['title']}", "subject": "psychology",
                            "pitch": ", ".join(part["points"][:4]) + "…", "source": f"{b['book']} by {b['author']}",
                            "series": b["id"], "part": n, "button": f"{b['author'].split()[-1]} {n}/{total}: {part['title']}"})
                break
    return out[:limit]


def book_part(series: str, n: int) -> dict:
    b = next(x for x in load_series() if x["id"] == series)
    return {**b, "n": n, "total": len(b["parts"]), **b["parts"][n - 1]}


def propose_long(used: list[str]) -> list[Topic]:
    prompt = (
        f"Propose 5 topics for a 2-3 hour 'fall asleep learning' video. Each must be broad enough for "
        f"{CFG['long']['chapters']} calm chapters, evergreen, searchable (people search e.g. 'fall asleep "
        f"learning about the ocean'). Mix subjects from: {', '.join(CFG['subjects'])}.\n"
        f"Do NOT repeat or closely overlap these already used topics: {used[-60:] or 'none'}."
    )
    return llm.json(prompt, Topics, system=CHANNEL_VOICE).topics[:5]


class ShortIdea(BaseModel):
    fact: str = Field(description="the idea or surprising fact in one sentence")
    subject: str
    related_long: int = Field(description="index in the provided long-video list this comes from, or -1")
    book: str = Field(default="", description="book id from the book list this idea comes from, or empty")


class ShortIdeas(BaseModel):
    ideas: list[ShortIdea]


def propose_shorts(longs: list[dict], used_facts: list[str]) -> list[ShortIdea]:
    """Идеи шортсов: из книг владельца (assets/book_series.yaml) и из фактов наших длинных видео."""
    books = "\n\n".join(f"Book id={b['id']}: {b['book']} by {b['author']} (evidence: {b['evidence']})\n"
                         + "\n".join(f"- {pt}" for part in b["parts"] for pt in part["points"])
                         for b in load_series())
    listing = "\n\n".join(f"Video {i}: {v['title']}\n" + "\n".join(f"- {f}" for f in v["facts"])
                           for i, v in enumerate(longs))
    prompt = (
        "Pick 5 ideas for 35-55 second YouTube Shorts. Sources — key ideas of these books:\n"
        f"{books}\n\n" + (f"and facts our long videos already cover:\n{listing}\n\n" if longs else "")
        + "Choose the most scroll-stopping ones (the 'wait, that's me!' reaction): everyday habits explained, "
        "counter-intuitive findings, things viewers recognise in themselves. Rephrase each as one punchy sentence "
        "that names the author, keep it accurate, and when it is an author's model rather than research, say so "
        "(e.g. 'Eric Berne claimed...'). Set book to the book id (or related_long to the video number). "
        "Use 5 different books.\n"
        f"Avoid these already used ideas: {used_facts[-80:] or 'none'}."
    )
    return llm.json(prompt, ShortIdeas, system=CHANNEL_VOICE).ideas[:5]


class Facts(BaseModel):
    facts: list[str] = Field(description="standalone one-sentence facts or ideas from the script")


def short_facts(chapters: list[dict]) -> list[str]:
    """15 самых цепляющих фактов из готового сценария длинного видео — материал для будущих шортсов."""
    text = "\n\n".join(f"## {c['title']}\n{c['text']}" for c in chapters)
    return llm.json(
        "From this sleep-video script, list the 15 most surprising, concrete facts, experiments or ideas that "
        "would make a great 30-55 second Short. Each one sentence, self-contained, faithful to the script "
        "(keep attributions like 'according to Kahneman').\n\n" + text, Facts, system=CHANNEL_VOICE).facts[:15]


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
    "You are a strict science, history and psychology fact-checker for an educational YouTube channel. Check every "
    "factual claim (numbers, dates, names, causal explanations, superlatives like 'largest' or 'first'). "
    "Flag false, misleading, outdated or popular-myth claims, including pop-psychology findings that failed "
    "to replicate (e.g. power posing, ego depletion, behavioral priming) when presented as settled fact. Do not flag style. If everything is "
    "accurate, return an empty list."
)


def fact_check(text: str) -> list[Issue]:
    res = llm.json(f"Fact-check this narration script:\n\n{text}", FactCheck, system=CHECKER, role="checker")
    return [i for i in res.issues if i.severity in ("high", "medium")]


class Fixed(BaseModel):
    script: str = Field(description="the corrected script ONLY: words to be read aloud, no notes or comments")


def _fix(text: str, issues: list[Issue], extra: str = "") -> str:
    notes = "\n".join(f"- \"{i.claim}\" → {i.problem}. Correct: {i.correction}" for i in issues)
    limit = round(len(text.split()) * 1.1) + 3
    fixed = llm.json(
        f"Rewrite the script below fixing ONLY these factual problems, keeping length, tone and structure. "
        f"Prefer softening or removing a wrong claim over adding citations, dates or caveats; "
        f"stay under {limit} words. {extra}Every word of `script` is read aloud on air, so never put notes, "
        f"comments or explanations there.\n\nProblems:\n{notes}\n\nScript:\n{text}",
        Fixed, system=CHANNEL_VOICE).script.strip()
    return fixed if len(fixed.split()) <= limit * 1.3 else text


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
    hero_image: str = Field(description="ONE illustration for the whole video: a single central subject that "
                            "embodies the topic and can move gently (jellyfish, drifting planet, candle flame, "
                            "slowly turning globe, floating cell...), dark calm background, no text")
    motion: str = Field(description="how the subject moves slowly and continuously in a seamless loop, one "
                        "sentence, e.g. 'the jellyfish pulses softly and drifts in place, tentacles trailing'")
    chapters: list[ChapterPlan]


def long_outline(topic: str, feedback: str = "", book: dict | None = None) -> Outline:
    hours = round(CFG["long"]["target_minutes"] / 60, 1)
    if book:
        about = (f"part {book['n']} of {book['total']} of a calm retelling of the book '{book['book']}' by "
                 f"{book['author']}: '{book['title']}'. Cover, in order: {'; '.join(book['points'])}. "
                 f"How to present the evidence: {book['evidence']}. Retell the ideas in your own words with your own "
                 f"examples; never reproduce the book's text. Title format: 'Fall Asleep to {book['book']} by "
                 f"{book['author']} (Part {book['n']} of {book['total']}): <Part title> | {math.ceil(hours)} Hours "
                 f"Calm Book Summary' — at most 100 characters, shorten the book title if needed.")
    else:
        about = (f"{topic}. Title format: "
                 f"'Fall Asleep Learning: <Topic> | {math.ceil(hours)} Hours of Calm Science Facts'.")
    return llm.json(
        f"Plan a {hours}-hour sleep-learning video: {about} Exactly {CFG['long']['chapters']} chapters "
        f"that flow gently from simple to deeper, ending in a soft, reflective chapter. "
        f"15-25 tags.{(' Owner feedback on previous version: ' + feedback) if feedback else ''}",
        Outline, system=CHANNEL_VOICE)


def long_chapter(outline: Outline, idx: int, feedback: str = "") -> str:
    L = CFG["long"]
    seconds = L["target_minutes"] * 60 / L["chapters"]
    words = int(seconds / 60 * L["words_per_minute"])
    ch = outline.chapters[idx]
    first, last = idx == 0, idx == len(outline.chapters) - 1
    plan = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(outline.chapters))
    return llm.text(
        f"Video: {outline.video_title}\nChapters:\n{plan}\n\n"
        f"Write chapter {idx + 1}: '{ch.title}', covering: {'; '.join(ch.points)}.\n"
        f"Length: about {words} words (this is important — it fills {seconds / 60:.0f} minutes).\n"
        "Style: slow, warm, soothing bedtime narration. Second person ('imagine...'). Long flowing sentences, "
        "gentle transitions, no exclamation marks, no questions to the viewer that demand attention, no "
        "lists or headings, no sudden excitement. Explain each fact clearly as a good teacher would. "
        + ("Open with a short calm welcome inviting the listener to get comfortable. " if first else "")
        + ("End by slowly winding down, wishing the listener restful sleep. " if last else "")
        + "Output only the narration as plain paragraphs, no title."
        + (f" Owner feedback on previous version: {feedback}" if feedback else ""),
        system=CHANNEL_VOICE, max_tokens=20000)


# ---------- шортс ----------

class Scene(BaseModel):
    words: str = Field(description="the exact narration fragment this visual covers")
    visual: str = Field(description="image prompt describing ONLY what is in the frame: one big concrete subject "
                        "and its action, close-up or medium shot, no text, signs, labels or names to draw. "
                        "Do NOT mention art style, colors, "
                        "lighting or mood — the channel's visual style is added automatically")
    stock_query: str = Field(description="2-3 word stock video search query")


class ShortScript(BaseModel):
    hook_text: str = Field(default="", description="on-screen hook for the first 1.5 s, max 6 words, ALL CAPS")
    narration: str = Field(description="full voiceover, starts with the hook sentence")
    scenes: list[Scene]
    title: str = Field(description="YouTube Shorts title, <70 chars, curiosity-driven, includes the searchable "
                       "author or concept name (e.g. 'Kahneman', 'loss aversion'), ends with 2 topical hashtags")
    description: str = Field(description="2-3 sentences; credits the book and author when based on a book")
    tags: list[str]
    character: str = Field(default="", description="tale format: fixed visual description of the protagonist")
    kicker: str = Field(default="", description="series format: tiny caps line above the title")
    header_title: str = Field(default="", description="series format: on-screen title, max 8 words")


def short_script(fact: str, long_title: str | None, feedback: str = "", fmt: dict | None = None,
                 book: dict | None = None, fixed: str = "") -> ShortScript:
    """fmt — формат из assets/short_style.yaml (tale/series), book — запись из book_series.yaml."""
    n = CFG["shorts"]["images_per_short"]
    src = (f"The idea comes from the book '{book['book']}' by {book['author']} (evidence: {book['evidence']}). "
           "Pick its most surprising, talked-about angle (what readers quote and argue about). Retell it in your "
           "own words and credit the author; you may include ONE short memorable quote from the book "
           "(max 20 words, attributed) only if you are sure of its exact wording. " if book else "")
    how = (fmt["script"] + (f"\nhero: {fmt['hero']}" if fmt.get("hero") else "")) if fmt else (
        "Audience: ADHD-style, short attention. First sentence is a hook that lands in under 1.5 seconds. "
        "Then rapid, punchy sentences, one idea each, and a satisfying last line. 95-130 words.")
    if fixed:  # готовый сценарий владельца/редактора: не переписывать, только раскадровка и метаданные
        how += ("\nThe narration is ALREADY WRITTEN — copy it into `narration` exactly, word for word, and build "
                f"the scenes, title and metadata around it:\n{fixed}")
    return llm.json(
        f"Write a YouTube Short about this idea: {fact}\n{src}\n{how}\n"
        "Funny or moving is good; wrong is not — every claim must be true. "
        + (f"End by mentioning the full calm 'fall asleep learning' video on this topic on our channel ('{long_title}'). "
           if long_title else "")
        + f"Split into {n - 2}-{n + 2} scenes (about 2-4 seconds each), each visual showing exactly what is "
        "said at that moment, with action and movement in the frame."
        + (f" Owner feedback on previous version: {feedback}" if feedback else ""),
        ShortScript, system=CHANNEL_VOICE)


class Translation(BaseModel):
    lines: list[str] = Field(description="Russian translation of each input line, same count and order")


def translate_ru(lines: list[str], glossary: str = "") -> list[str]:
    """Русские субтитры: перевод по фразам, число строк сохраняется (тайминги — от английской озвучки)."""
    numbered = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))
    res = llm.json(
        "Translate these consecutive subtitle lines of an English YouTube Short into natural, conversational "
        "Russian for on-screen subtitles. Keep exactly the same number of lines and order; each Russian line "
        "must match its English line in meaning (do not move words between lines). Short and readable, "
        "keep book titles in their known Russian editions or in English if unsure, keep author names in Russian "
        f"spelling.{(' Use exactly this official Russian wording where it applies: ' + glossary) if glossary else ''}"
        f"\n\n{numbered}", Translation)
    out = res.lines[:len(lines)]
    return out + lines[len(out):]


def channel_names() -> list[str]:
    return llm.json(
        "Suggest 5 memorable YouTube channel names for a calm sleep-learning science channel (long 'fall "
        "asleep learning' videos + weird-fact Shorts). Short, easy to spell, available-sounding, English.",
        Names).names[:5]
