import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLANK_SCREEN = os.path.join(ROOT, "assets", "blank_screen.png")
END_CARD = os.path.join(ROOT, "assets", "thank_you_card.png")

API_URL = "https://api.openai.com/v1"
CHARS_PER_SECOND = 14.0
INTRO_SECONDS = 2
END_SECONDS = 3
THANKS_TEXT = "Thank you."
THANKS_DELAY = 0.4
TITLE_VOICE_TEMPLATE = "This video demonstrates {title}."
TITLE_VOICE_DELAY = 0.4
TITLE_VOICE_TAIL = 0.6

DEFAULT_TRANSCRIBE_PROMPT = (
    "Verbatim transcript of a narrated screen-recording walkthrough of a software or mobile app. "
    "Transcribe exactly what is spoken with correct punctuation. Capitalise button and screen names."
)

DEFAULT_STYLE = (
    "Speak in a natural, warm, conversational tone with human pacing and gentle expression, "
    "like a friendly professional explaining a product walkthrough. "
    "Always finish each sentence completely and clearly."
)

IMPROVE_SYSTEM = """You are a strict script editor for narrated software demo videos.
You receive a machine transcript of a spoken screen-recording walkthrough as JSON segments, each with: id, start, end, slot_seconds, max_chars, text. You also receive an optional "context" string with the video title, glossary and terminology rules that you MUST follow exactly.
Rewrite every segment into clear, polished, natural voice-over narration for a professional demo video.

RULES (all mandatory):
1. Output ONLY valid JSON of the form {"segments":[{"id":<int>,"text":"<string>"}]}. Same ids, same order, same count as the input. No other keys, no commentary.
2. HARD LIMIT: the character length of each text must be <= that segment's max_chars. Count characters. Prefer shorter.
3. Every text must be a complete, grammatical sentence or clause that ends cleanly and can be spoken fully within slot_seconds. Never leave a sentence unfinished or cut off. A clause that deliberately continues into the next segment may end with a comma.
4. GRAMMAR CHECK: correct grammar, spelling, punctuation, tense and agreement in every line so it reads as fluent, natural, grammatically correct narration. Use the context to choose the right terms and spellings.
5. Preserve the meaning and the order of the steps. Do NOT invent features, buttons, screens, numbers or steps that are not supported by the transcript. No marketing claims.
6. Fix obvious speech-to-text errors only when the surrounding steps or the context make the correct wording clear. Otherwise keep the original wording.
7. Use second-person, present tense, instructional voice (for example "Click Continue."). Capitalise UI element names.
8. The video has a separate title card with its own voice and a separate Thank You end card with its own voice. Never add a greeting, a welcome, a thank-you or a goodbye to any segment.
9. If slot_seconds is below 1.5 and the meaning cannot fit, you may return "" for that segment, but only after moving its meaning into the previous segment when that segment has room.
10. Plain spoken text only: no markdown, no emojis, no quotation marks, no stage directions, no timestamps.
11. Apply the context glossary and terminology exactly as given.
12. "second_transcription" is an independent transcription of the same audio without timestamps. Use it only as evidence: when a segment text looks like a mishearing and the second transcription clearly shows the better wording for the same passage, use that wording. Never copy lines from it that have no matching segment, and never let it change the number or order of segments.
13. Segment texts may contain pause markers such as [pause 2s] or [pause]. Keep every marker exactly as written and in the same place between the same words. Never add, move or remove a marker."""

SHORTEN_SYSTEM = """You shorten one line of narration for a software demo voice-over.
Return ONLY valid JSON {"text":"<string>"}.
RULES: the result must be at most max_chars characters; it must be a complete, grammatical sentence or clause that ends cleanly; keep the original meaning and all key UI names; do not add new information; plain spoken text only; follow the context glossary exactly; keep any [pause 2s] marker exactly as written in the same place."""

REVISE_SYSTEM = """You revise an already approved narration script for a software demo video according to a human reviewer's instruction.
You receive JSON with "reviewer_feedback", an optional "context" (glossary rules to keep following), and "segments", each with id, slot_seconds, max_chars and the current text.

RULES:
1. The reviewer_feedback is the top priority. Apply it to EVERY line it concerns, not just some of them. If it asks for a style change (for example formal tone, no contractions, shorter, friendlier), every single line must follow it.
2. Before answering, re-read every line and verify it complies with the feedback. Fix any line that does not. For "no contractions", no line may contain "'s" meaning "is/us", "n't", "'re", "'ll", "'ve" or "'d" (write "let us", "do not", "you are").
3. Keep each line's meaning and the order of the steps. Do not add or remove steps. Keep UI names and the context glossary.
4. Every text must be a complete grammatical sentence or clause, at most max_chars characters, and speakable within slot_seconds.
5. Plain spoken text only: no markdown, quotation marks or emojis.
6. Output ONLY valid JSON {"segments":[{"id":<int>,"text":"<string>"}]} with the same ids, order and count as the input.
7. Keep any pause marker such as [pause 2s] exactly as written, in the same place between the same words, unless the reviewer explicitly asks to change the pauses."""

TITLE_INTRO_SYSTEM = """You decide whether the very first sentence of a narrated software demo transcript only announces the overall topic of the video, so that it duplicates the video's title. The title is spoken separately on a title card as "This video demonstrates <title>".
Return ONLY valid JSON {"remove": true or false, "text": "<the first sentence copied exactly, or an empty string>"}.
RULES:
1. Set remove to true ONLY when the first sentence is a pure introduction or announcement of the topic (for example "In this video we will see X", "Welcome, today we look at X", "This flow shows X") AND X is the same subject as the title.
2. If the sentence contains any instruction, step, on-screen action or extra information beyond announcing the topic, set remove to false.
3. If the subject differs from the title or you are unsure, set remove to false.
4. "text" must be copied character for character from the first line and cover only that first sentence. If remove is false, text is an empty string."""

CLOSING_THANKS_SYSTEM = """You find sign-off sentences at the END of a narrated software demo transcript. A separate "Thank You" end card with its own voice replaces them.
You receive the last lines of the transcript. Return ONLY valid JSON {"texts": ["<sentence>", ...]}.
RULES:
1. Include a sentence only if it is purely a thank-you or goodbye to the viewer, for example "Thank you.", "Thanks for watching.", "Thank you for your time.", "See you next time." It must contain no instruction and no information.
2. Do NOT include sentences that state an outcome or a last step, for example "The delivery is completed." or "Click finish."
3. Copy each sentence character for character from the lines, one sentence per entry.
4. If there is no pure sign-off sentence, return {"texts": []}."""
