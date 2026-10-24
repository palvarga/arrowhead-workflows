#!/usr/bin/env python3
"""Intent Translators. Both return a candidate {"interpretation": {...}, "recipe": {...} | None}.

  StoredTranslator  replays stored drafts (drafts/<intent>/round<k>.json); deterministic, no network.
  ClaudeTranslator  calls Claude through the Anthropic Messages API (stdlib urllib only).
                    Needs ANTHROPIC_API_KEY; model from --model or ANTHROPIC_MODEL (default below).
"""
import json, os, pathlib, re, time, urllib.request, urllib.error

HERE = pathlib.Path(__file__).parent
DEFAULT_MODEL = "claude-opus-5-5"
API_URL = "https://api.anthropic.com/v1/messages"


class StoredTranslator:
    name = "stored"

    def __init__(self, root=HERE / "drafts"):
        self.root = pathlib.Path(root)

    def propose(self, intent, ctx, feedback=None, round_no=0):
        d = self.root / intent["id"]
        files = sorted(d.glob("round*.json"), key=lambda p: int(p.stem[5:]))
        if not files:
            raise FileNotFoundError(f"no stored drafts for {intent['id']} in {d}")
        f = files[min(round_no, len(files) - 1)]
        c = json.loads(f.read_text(encoding="utf-8"))
        return {"interpretation": c.get("interpretation"), "recipe": c.get("recipe"),
                "source": str(f.relative_to(HERE)), "note": c.get("note")}


def context_for_prompt(ctx):
    """The parts of Ctx_i the Translator sees: services, Executors, engineering facts, current runtime state.
    The machine-readable validator fields are not shown, so the model must reason from the facts."""
    return {
        "services": {k: v["text"] for k, v in ctx["K_AH"]["services"].items()},
        "executors": {k: v["text"] for k, v in ctx["K_AH"]["executors"].items()},
        "engineering_facts": [f"{f['id']}: {f['text']}" for f in ctx["K_eng"]["facts"]],
        "runtime": ctx["K_runtime"],
    }


def extract_json(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(t[start:end + 1])


class ClaudeTranslator:
    name = "claude"

    def __init__(self, model=None, api_key=None, max_tokens=8000, log_dir=None, timeout=300):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.max_tokens, self.timeout = max_tokens, timeout
        self.log_dir = pathlib.Path(log_dir) if log_dir else None
        self.system = (HERE / "prompts" / "translator_system.md").read_text(encoding="utf-8")
        self.fmt = (HERE / "prompts" / "output_format.md").read_text(encoding="utf-8")
        self.history = {}

    def _call(self, messages):
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": self.system, "messages": messages}
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 529) and attempt < 2:
                    time.sleep(5 * (attempt + 1)); continue
                raise RuntimeError(f"Anthropic API HTTP {e.code}: {msg}") from None

    def propose(self, intent, ctx, feedback=None, round_no=0):
        msgs = self.history.setdefault(intent["id"], [])
        if round_no == 0:
            msgs.clear()
            msgs.append({"role": "user", "content":
                "<context>\n" + json.dumps(context_for_prompt(ctx), indent=1) + "\n</context>\n\n"
                + self.fmt + "\n\n<intent>\n" + intent["text"] + "\n</intent>"})
        else:
            failed = [f for f in (feedback or []) if not f["ok"]]
            msgs.append({"role": "user", "content":
                "The Recipe Validator returned the draft with these findings:\n"
                + json.dumps(failed, indent=1) + "\nReturn the complete revised JSON object."})
        resp = self._call(msgs)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        msgs.append({"role": "assistant", "content": text})
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            (self.log_dir / f"{intent['id']}.round{round_no}.response.json").write_text(json.dumps(resp, indent=1), encoding="utf-8")
        try:
            c = extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            return {"interpretation": None, "recipe": None, "source": f"{self.model} (unparseable: {e})",
                    "raw": text}
        return {"interpretation": c.get("interpretation"), "recipe": c.get("recipe"),
                "source": f"{self.model}, response id {resp.get('id')}", "usage": resp.get("usage")}
