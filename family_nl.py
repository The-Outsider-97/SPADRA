"""A requirement in words -> a typed family draft (report 8.2), without a language model.

This is a fixed set of patterns over English text, the same kind of interpreter the assistant already uses (the SLAI Language Agent is not available here, see
``agent_hub``). It does not understand language in general; it recognises a small vocabulary and says what it recognised, what it did not, and what it had to
leave open. The report's rule is kept literally: *the interpreter returns uncertainty and unresolved choices and never chooses engineering parameters invisibly*.

  interpret(text)  ->  a draft: the family definition, every value with where it came from (explicit in the text / inferred / template default), the unresolved choices,
                       the parts of the request that are not modelled, and a computed preview of the derived values. Nothing is created.
  propose(project, text, accept=..., overrides=...)  ->  a reviewable proposal (``Assistant.propose_commands``: dry-run on a scratch copy, then a person's consent). It is
                       refused while a blocking choice is neither given (``overrides``) nor explicitly accepted (``accept``).

Only verified SGEO generator modes are used (a box family; ``FM.define`` checks that the family generates). A family's lights are produced for placed windows by the
``procedural.lights`` group, not by a custom B-rep in the family; the draft says so. The vocabulary is windows, doors and rectangular beams, matching the templates."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from . import families as FM
from . import family_templates as FT

INTERPRETER = {"kind": "rule_based_patterns", "language_model": False, "version": 1,
               "note": "A fixed pattern interpreter, not a language model: anything it does not recognise is listed, not guessed."}
KINDS = {"window": ("window", "casement", "fenestration"), "door": ("door", "doorset"), "beam": ("beam", "girder", "joist")}
_WORDS = {"one": 1, "single": 1, "two": 2, "double": 2, "three": 3, "triple": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(?:(mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|met(?:er|re)s?|m)(?![a-z]))"
_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
# Things a person may ask for that a box family with typed parameters cannot represent. Reported, never silently dropped.
_NOT_MODELLED = {
    r"\b(?:triple|double)[- ]glaz": "glazing build-up (layers, gas fill) is not part of the family",
    r"\bu[- ]?value|thermal|insulat|energy": "thermal performance is not computed by a family; it needs a separate analysis",
    r"\bfire|acoustic|sound|rated\b": "fire / acoustic ratings are not a family parameter and are not checked",
    r"\bcurved|arch(?:ed)?\b|round(?:ed)?\b|circular": "curved or arched outlines need geometry the box generator does not have",
    r"\bsliding|pivot|casement opening|tilt|hung\b": "the way it opens is not part of the family (hinge and swing are set on a placed door)",
    r"\baccessib|wheelchair|ada\b|code[- ]compliant|compliant": "compliance with a code or accessibility rule is not checked here",
    r"\bcolou?r|paint|finish|texture|material|timber|steel|aluminium|aluminum|pvc|concrete": "materials and finishes are set on placed elements, not in the family",
    r"\bload|stress|deflection|support|cantilever": "structural loads and supports are not part of the family; use the beam analysis and optimization",
}


# ------------------------------------------------------------------------------------------------------------------------------ number reading
def _num(token: str) -> float:
    return float(token.replace(",", "."))


def _mm(value: str, unit: str | None, default_unit: str = "mm") -> float:
    u = (unit or default_unit).lower()
    key = "mm" if u.startswith("mil") or u == "mm" else "cm" if u.startswith("cen") or u == "cm" else "m"
    return _num(value) * _TO_MM[key]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _count(word: str) -> int | None:
    word = word.lower()
    return int(word) if word.isdigit() else _WORDS.get(word)


# ------------------------------------------------------------------------------------------------------------------------------ the draft
class _Draft:
    def __init__(self, text: str):
        self.text = text
        self.low = text.lower()
        self.found: dict[str, dict[str, Any]] = {}              # parameter -> {value, basis, source}
        self.ranges: dict[str, tuple[float, float, str]] = {}
        self.adjustable: dict[str, str] = {}
        self.assumptions: list[str] = []
        self.unresolved: list[dict[str, Any]] = []
        self.ignored: list[dict[str, str]] = []

    def note(self, name: str, value: Any, basis: str, source: str) -> None:
        self.found[name] = {"value": value, "basis": basis, "source": source}


def _kind(d: _Draft) -> str | None:
    hits = {k for k, words in KINDS.items() if any(re.search(rf"\b{w}s?\b", d.low) for w in words)}
    return next(iter(hits)) if len(hits) == 1 else None


def _kinds_mentioned(d: _Draft) -> list[str]:
    return sorted(k for k, words in KINDS.items() if any(re.search(rf"\b{w}s?\b", d.low) for w in words))


def _read_panels(d: _Draft) -> None:
    for m in re.finditer(r"\b(\d+|one|single|two|double|three|triple|four|five|six|seven|eight)[- ](?:panel|panels|panelled|paneled|light|lights|pane|panes|sash|sashes|leaf|leaves)\b", d.low):
        n = _count(m.group(1))
        if n is not None:
            d.note("panels", n, "explicit", m.group(0))
            return
    m = re.search(r"\b(?:panel|light|pane)\s*count\s*(?:of|=|:)?\s*(\d+)", d.low)
    if m:
        d.note("panels", int(m.group(1)), "explicit", m.group(0))


_LABELS = {"width": ("width", "wide"), "height": ("height", "high", "tall"), "depth": ("depth", "deep", "thick", "thickness"), "span": ("span", "long", "length")}
_BEAM_LABELS = {"width": ("width", "wide"), "height": ("height", "high", "tall", "deep", "depth"), "span": ("span", "long", "length")}      # a beam's section depth is its height; its span is the box's long axis


def _read_dimensions(d: _Draft, kind: str) -> None:
    target = {"width": "width", "height": "height", "depth": "depth", "span": "depth"}
    labels = _BEAM_LABELS if kind == "beam" else {k: v for k, v in _LABELS.items() if k != "span"}
    taken: set[str] = set()
    for label, words in labels.items():
        name = target[label]
        if name in taken:
            continue
        alt = "|".join(words)
        for pattern in (rf"{_NUM}\s*{_UNIT}?\s*(?:{alt})\b", rf"\b(?:{alt})\s*(?:of|=|:|is)?\s*{_NUM}\s*{_UNIT}?"):
            m = re.search(pattern, d.low)
            if m and not re.search(rf"(?:{alt})\s+(?:between|from)\b", m.group(0)):
                d.note(name, _mm(m.group(1), m.group(2)), "explicit", m.group(0))
                taken.add(name)
                break
    m = re.search(rf"{_NUM}\s*{_UNIT}?\s*(?:x|×|by)\s*{_NUM}\s*{_UNIT}?(?:\s*(?:x|×|by)\s*{_NUM}\s*{_UNIT}?)?", d.low)
    if m and not ({"width", "height"} & taken):
        unit = m.group(2) or m.group(4) or m.group(6)
        values = [_mm(m.group(i), unit or "mm") for i in (1, 3) + ((5,) if m.group(5) else ())]
        names = ["width", "height"] if kind != "beam" else ["width", "height", "depth"]
        for n, v in zip(names, values):
            d.note(n, v, "inferred", m.group(0))
        d.assumptions.append(f"“{m.group(0).strip()}” was read as {' x '.join(names[:len(values)])} in that order")
        if not unit:
            d.assumptions.append("No unit was given for the sizes; millimetres were assumed")
    for label, words in labels.items():                                    # "width between 600 and 1200 mm", "height from 1 to 1.5 m"
        name = target[label]
        alt = "|".join(words)
        m = re.search(rf"\b(?:{alt})\s*(?:between|from)\s*{_NUM}\s*{_UNIT}?\s*(?:and|to|-)\s*{_NUM}\s*{_UNIT}?", d.low)
        if m:
            unit = m.group(4) or m.group(2)
            d.ranges[name] = (_mm(m.group(1), unit), _mm(m.group(3), unit), m.group(0))


def _read_members(d: _Draft, kind: str) -> None:
    patterns = {"window": [("frame_width", r"frame(?:\s*(?:width|thickness|member))?"), ("mullion", r"mullions?")],
                "door": [("frame_thickness", r"frame(?:\s*(?:width|thickness|member))?"), ("nominal_clearance", r"(?:nominal\s*)?clearance")]}
    for name, word in patterns.get(kind, []):
        for pattern in (rf"{word}\s*(?:of|=|:|is)?\s*{_NUM}\s*{_UNIT}?", rf"{_NUM}\s*{_UNIT}?\s*{word}\b"):
            m = re.search(pattern, d.low)
            if m:
                d.note(name, _mm(m.group(1), m.group(2)), "explicit", m.group(0))
                break
    if kind == "window":
        m = re.search(rf"max(?:imum)?\s*(?:panel|light|pane)\s*(?:span|width)\s*(?:of|=|:|is)?\s*{_NUM}\s*{_UNIT}?", d.low)
        if m:
            d.note("max_panel_span", _mm(m.group(1), m.group(2)), "explicit", m.group(0))


def _read_adjustable(d: _Draft, kind: str) -> None:
    for m in re.finditer(r"\b(adjustable|configurable|variable|resizable|parametric|editable)\b\s*([^,.;]*)", d.low):
        tail = m.group(2)
        named = [name for name, words in (("width", ("width",)), ("height", ("height",)), ("depth", ("depth", "thickness")), ("panels", ("panel count", "number of panels", "panels", "number of lights")),
                                          ("frame_width" if kind == "window" else "frame_thickness", ("frame",)), ("mullion", ("mullion",)))
                 if any(w in tail for w in words)]
        if named:
            for n in named:
                d.adjustable[n] = m.group(0).strip()
        elif m.group(1) in ("adjustable", "resizable", "variable"):
            d.adjustable.setdefault("*", m.group(0).strip())


def _read_ignored(d: _Draft) -> None:
    for pattern, why in _NOT_MODELLED.items():
        m = re.search(pattern, d.low)
        if m:
            d.ignored.append({"phrase": m.group(0).strip(), "why": why})


# ------------------------------------------------------------------------------------------------------------------------------ building the family
_REQUIRED_SIZES = {"window": ("width", "height"), "door": ("width", "height"), "beam": ("width", "height", "depth")}
_BLOCKING_MEMBERS = {"window": ("frame_width",), "door": ("frame_thickness", "nominal_clearance"), "beam": ()}
_QUESTIONS = {
    "width": "What overall width should the default be?", "height": "What overall height should the default be?",
    "depth": "What is the span / thickness (the long or through-wall dimension)?",
    "frame_width": "What frame member width should be used? It sets the glazed span and every light's width.",
    "frame_thickness": "What frame member thickness should be used? It sets the clear width and must stay under half the width.",
    "nominal_clearance": "What nominal clearance should the clearance budget subtract? (an illustrative figure, not a free-passage rule)",
    "mullion": "What width should each internal mullion have? It reduces every light's width.",
    "panels": "How many lights (panels) should the window have?",
}


def interpret(text: Any, *, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The draft for a requirement in words. Pure: it reads text and the templates and creates nothing. ``overrides`` gives values (mm, or a count for ``panels``) for the
    parameters the first draft left open, so a person answers the questions rather than the interpreter guessing."""
    request = _clean(text)
    if not request or len(request) > 2000:
        raise BadRequest("Describe the family in 1 to 2000 characters")
    d = _Draft(request)
    mentioned = _kinds_mentioned(d)
    kind = _kind(d)
    base = {"request": request, "interpreter": INTERPRETER}
    if kind is None:
        why = "The request names more than one kind of family" if len(mentioned) > 1 else "The request does not name a family this interpreter knows"
        return {**base, "status": "unsupported", "kind": None, "reason": why, "supported": {k: list(w) for k, w in KINDS.items()}, "commit_allowed": False,
                "hint": "Say which of these to create (one at a time), e.g. “a three-panel window with adjustable width and height”"}
    template = deepcopy(FT.TEMPLATES[kind])
    params = template["parameters"]
    if kind == "window":
        _read_panels(d)
    _read_dimensions(d, kind)
    _read_members(d, kind)
    _read_adjustable(d, kind)
    _read_ignored(d)
    for name, value in (overrides or {}).items():
        if name not in params and name != "max_panel_span":
            raise BadRequest(f"'{name}' is not a parameter of this {kind} draft", details={"parameters": [n for n, s in params.items() if "expression" not in s]})
        d.note(name, int(value) if name == "panels" else float(value), "given", "answered by the person")
    if kind == "door" and d.found.get("frame_thickness") and d.found.get("width") and 2 * d.found["frame_thickness"]["value"] >= d.found["width"]["value"]:
        raise BadRequest("The frame thickness must be less than half the width", hint="A door's clear width is the width minus two frames")

    # values and ranges --------------------------------------------------------------------------------------------------------
    widened: list[str] = []
    for name, info in d.found.items():
        if name == "max_panel_span" or name not in params:
            continue
        spec = params[name]
        spec["default"] = int(info["value"]) if spec.get("type") == "integer" else round(info["value"], 6)
        if spec.get("type") != "integer":
            for bound, pick in (("min", min), ("max", max)):
                if bound in spec and pick(spec[bound], spec["default"]) != spec[bound]:
                    widened.append(f"{name}: the template's {bound} of {spec[bound]:g} mm does not admit {spec['default']:g} mm, so the range was widened")
                    spec[bound] = spec["default"]
    for name, (lo, hi, source) in d.ranges.items():
        if name in params and "expression" not in params[name]:
            if lo >= hi:
                raise BadRequest(f"The range for {name} must have its minimum below its maximum", details={"text": source})
            params[name]["min"], params[name]["max"] = lo, hi
            default = params[name].get("default")
            if default is not None and not lo <= default <= hi:
                params[name]["default"] = min(max(default, lo), hi)
                d.assumptions.append(f"The default {name} was moved into your range, to {params[name]['default']:g} mm")
            d.note(f"{name}_range", [lo, hi], "explicit", source)

    if kind == "window":
        panels = d.found.get("panels")
        if panels and not 1 <= panels["value"] <= params["panels"]["max"]:
            raise BadRequest(f"A window here has 1 to {params['panels']['max']} lights", details={"text": panels["source"]})
    # adjustable parameters become instance-level (a placed element may differ), everything else stays at the type -------------
    chosen = set(d.adjustable) - {"*"}
    if "*" in d.adjustable and not chosen:
        chosen = {"width", "height"}
        d.assumptions.append("“adjustable” was given without saying which sizes: width and height were taken")
    for name, spec in params.items():
        if "expression" in spec or spec.get("type") == "boolean":
            continue
        spec["scope"] = "instance" if name in chosen else "type"
    if not chosen:
        d.assumptions.append("No parameter was asked to be adjustable, so every size is fixed per type; say which should vary per instance")

    # the optional span rule -----------------------------------------------------------------------------------------------------
    if kind == "window" and d.found.get("max_panel_span"):
        params["max_panel_span"] = {"default": round(d.found["max_panel_span"]["value"], 6), "min": 100, "max": 4000, "scope": "type", "description": "Largest light width allowed"}
        params["span_ok"] = {"type": "boolean", "expression": "light_width <= max_panel_span", "description": "False when the lights come out wider than the maximum span"}
        params["light_width"]["max"] = params["max_panel_span"]["default"]

    # unresolved choices: whatever was not stated, never filled in silently ---------------------------------------------------------
    needed = [*_REQUIRED_SIZES[kind], *_BLOCKING_MEMBERS[kind], *(("panels", "mullion") if kind == "window" else ())]
    for name in needed:
        if name == "mullion" and d.found.get("panels") and d.found["panels"]["value"] == 1:
            continue
        if name in d.found:
            continue
        spec = params[name]
        unit = "" if spec.get("type") == "integer" else "mm"
        d.unresolved.append({"parameter": name, "blocking": True, "question": _QUESTIONS[name], "template_default": spec["default"], "unit": unit,
                             "why": "A value that changes the geometry or a derived value was not stated; the template default is only a starting point"})
    if kind == "window" and not d.found.get("max_panel_span"):
        d.unresolved.append({"parameter": "max_panel_span", "blocking": False, "question": "Is there a maximum light (panel) span? None is enforced unless one is given.",
                             "template_default": None, "unit": "mm", "why": "Glass size limits are an engineering choice; the draft adds no limit by itself"})
    for text_ in widened:
        d.unresolved.append({"parameter": text_.split(":")[0], "blocking": True, "question": f"Accept this wider range? {text_}", "template_default": None, "unit": "mm",
                             "why": "A requested value fell outside the template's range"})
    if kind == "door" and re.search(r"\b(left|right)[- ]hand|\bhinge|\bswing", d.low):
        d.ignored.append({"phrase": "hinge / swing", "why": "handing is set on a placed door (hinge start/end, swing left/right), not in the family"})
    if kind == "beam" and not any(i["why"].startswith("structural") for i in d.ignored):
        d.assumptions.append("The beam family holds the section geometry only (A = b h, I = b h^3 / 12 for an ideal rectangle); loads, supports and material are not part of it")

    family = {"name": template["name"], "shape": template["shape"], "parameters": params}
    preview = _preview(family)
    blocking = [u for u in d.unresolved if u["blocking"]]
    status = "invalid" if not preview["ok"] else "needs_input" if blocking else "draft"
    return {**base, "status": status, "kind": kind, "template": kind, "family": family,
            "extracted": [{"parameter": n, **i} for n, i in d.found.items()], "adjustable": sorted(chosen), "fixed_per_type": sorted(n for n, s in params.items() if s.get("scope") == "type"),
            "derived": [n for n, s in params.items() if "expression" in s],
            "generator": {"kind": "box", "verified": True, "note": ("A box family. The individual lights of a window are produced for each placed window by the procedural.lights group, "
                                                                    "not by custom geometry in the family." if kind == "window" else "A box family; the generator is one of the verified SGEO modes.")},
            "validation_rules": [f"{n}: {s['expression']}" + (f" (at least {s['min']:g})" if "min" in s else "") for n, s in params.items() if "expression" in s and not s.get("type") == "boolean"],
            "assumptions": d.assumptions, "unresolved": d.unresolved, "not_modelled": d.ignored, "preview": preview,
            "uncertainty": {"method": "pattern match", "explicit": sorted(n for n, i in d.found.items() if i["basis"] == "explicit"),
                            "inferred": sorted(n for n, i in d.found.items() if i["basis"] == "inferred"),
                            "template_default": sorted(n for n, s in params.items() if "expression" not in s and n not in d.found and s.get("type") != "boolean")},
            "commit_allowed": not blocking and preview["ok"],
            "next": (f"The defaults do not form a valid family: {preview.get('refused')}. Change the values that conflict (overrides) or the request." if not preview["ok"] else "Answer the blocking questions (overrides) or accept the template defaults explicitly (accept), then propose." if blocking else "Propose it: a person reviews the preview and approves.")}


def _preview(family: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the draft on a throwaway project: the derived values SGEO computes at the defaults, or the reason it refuses. Independent of the real project."""
    from .project import Project
    scratch = Project.create("preview")
    try:
        fid = FM.define(scratch, deepcopy(dict(family)))
    except BadRequest as exc:
        return {"ok": False, "refused": exc.message, "hint": getattr(exc, "hint", None)}
    fam = FM.family(scratch, fid)
    try:
        oid = scratch.model.instantiate(fid, {}, name="preview")
        resolved = FM.values(scratch, oid)
    except Exception as exc:                                                       # SGEO refuses the defaults: say why instead of showing a draft that cannot be placed
        return {"ok": False, "refused": f"The defaults cannot be evaluated: {exc}"}
    values = {n: FM._convert_out(spec, resolved.get(n)) for n, spec in fam["parameters"].items()}
    units = {n: ("mm" if spec["type"] == "length" else FM.DISPLAY_UNIT.get(spec["type"], "")) for n, spec in fam["parameters"].items()}
    return {"ok": True, "values": values, "units": units}


# ------------------------------------------------------------------------------------------------------------------------------ the proposal
def propose(project: Any, text: Any, *, accept: Sequence[str] = (), overrides: Mapping[str, Any] | None = None, assistant: Any = None) -> dict[str, Any]:
    """A reviewable proposal that creates the drafted family. Refused while a blocking question is neither answered (``overrides``) nor explicitly accepted at its template default
    (``accept``, a list of parameter names, or ``["all"]``). The proposal is dry-run on a scratch copy of the project; only a person's consent commits it."""
    draft = interpret(text, overrides=overrides)
    if draft["status"] == "unsupported":
        raise BadRequest(draft["reason"], hint=draft["hint"], details={"supported": draft["supported"]})
    if draft["preview"]["ok"] is False:
        raise BadRequest(f"The drafted family was refused: {draft['preview']['refused']}", hint=draft["preview"].get("hint"))
    accepted = set(accept or ())
    if any(not isinstance(a, str) for a in accepted):
        raise BadRequest("accept must be a list of parameter names")
    open_ones = [u for u in draft["unresolved"] if u["blocking"] and "all" not in accepted and u["parameter"] not in accepted]
    if open_ones:
        raise BadRequest("Some choices are still open: " + ", ".join(u["parameter"] for u in open_ones),
                         hint="Answer them in overrides, or accept the template defaults by naming them in accept",
                         details={"unresolved": open_ones})
    if assistant is None:
        from .assistant import ASSISTANT as assistant
    names = {f["name"] for f in FM.families(project)}
    base_name, n = draft["family"]["name"], 1
    while draft["family"]["name"] in names:                                       # a family's name is its identity here; never collide with (or overwrite) an existing one
        n += 1
        draft["family"]["name"] = f"{base_name} {n}"
    if n > 1:
        draft["assumptions"].append(f"A family named “{base_name}” already exists, so this one is called “{draft['family']['name']}”")
    taken = [u["parameter"] for u in draft["unresolved"] if u["blocking"] and ("all" in accepted or u["parameter"] in accepted)]
    lines = [f"Create the {draft['kind']} family “{draft['family']['name']}” from: {draft['request']}"]
    lines += [f"Assumption: {a}" for a in draft["assumptions"]]
    lines += [f"Accepted at its template default: {n}" for n in taken]
    lines += [f"Not modelled: {i['phrase']} ({i['why']})" for i in draft["not_modelled"]]
    msg = assistant.propose_commands(project, source="language-draft", title=f"Create a {draft['kind']} family from a description",
                                     explanation="\n".join(lines), steps=[{"op": "family.define", "params": draft["family"]}], risk="low",
                                     evidence={"draft": {k: draft[k] for k in ("kind", "extracted", "adjustable", "assumptions", "unresolved", "not_modelled", "uncertainty")},
                                               "interpreter": INTERPRETER}, evidence_kind="family_draft")
    return {"draft": draft, "proposal": msg}
