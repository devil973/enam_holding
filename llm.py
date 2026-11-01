"""Model routing. Cheap model for bulk extraction, strong model only where reasoning is needed.
Set ANTHROPIC_API_KEY. Swap this file to use any other provider; the rest of the repo only calls cheap() and strong()."""
import os, json, re
CHEAP = os.getenv("FINN_CHEAP_MODEL", "claude-haiku-5-5")
STRONG = os.getenv("FINN_STRONG_MODEL", "claude-sonnet-5-5")
_client = None
def _c():
    global _client
    if _client is None:
        import anthropic; _client = anthropic.Anthropic()
    return _client
def _call(model, prompt, max_tokens):
    r = _c().messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text")
def parse_json(text):
    """Models sometimes wrap JSON in fences or prose; pull out the first object."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no JSON in model output")
    return json.loads(m.group(0))
def cheap(prompt, max_tokens=1200): return parse_json(_call(CHEAP, prompt, max_tokens))
def strong(prompt, max_tokens=4000): return parse_json(_call(STRONG, prompt, max_tokens))
