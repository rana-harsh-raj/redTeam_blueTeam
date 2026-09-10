#!/usr/bin/env python3
"""_miniyaml.py — a deliberately minimal, stdlib-only YAML SUBSET parser.

config/generate.py must be stdlib-only per the task brief, which rules out
PyYAML. This module parses exactly the subset of YAML that
config/arena.yaml uses: block mappings, block sequences of mappings
("- key: value" followed by more-indented "key: value" lines), one-line
flow mappings ("{host: foo, port: 9400}"), scalars (bare word/number,
quoted string, true/false), and comments/blank lines. It is NOT a general
YAML parser -- no anchors, no multi-line strings, no nested flow
collections, no tabs. If arena.yaml ever needs a construct beyond this,
extend this parser deliberately rather than reaching for a pip dependency.
"""
import re

_FLOW_MAP_RE = re.compile(r"^\{(.*)\}$")
_KV_RE = re.compile(r"^([A-Za-z0-9_.\-]+):\s*(.*)$")


def _strip_comment_and_blank_lines(text):
    lines = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw:
            raise ValueError("miniyaml: tabs are not supported: %r" % raw)
        lines.append(raw.rstrip())
    return lines


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(val):
    val = val.strip()
    if val == "":
        return None
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        return val[1:-1]
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        return val[1:-1]
    if val == "true":
        return True
    if val == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    return val


def _parse_flow_mapping(val):
    m = _FLOW_MAP_RE.match(val.strip())
    if not m:
        raise ValueError("miniyaml: expected a flow mapping like {a: b}: %r" % val)
    inner = m.group(1)
    out = {}
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        kv = _KV_RE.match(part)
        if not kv:
            raise ValueError("miniyaml: bad flow mapping entry: %r" % part)
        out[kv.group(1)] = _parse_scalar(kv.group(2))
    return out


class _Cursor:
    def __init__(self, lines):
        self.lines = lines
        self.i = 0

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def advance(self):
        line = self.lines[self.i]
        self.i += 1
        return line


def _parse_value(val, cur, indent_gt):
    """val is the text after 'key:' on its own line (possibly empty, meaning
    a nested block follows on more-indented lines)."""
    val = val.strip()
    if val == "":
        nxt = cur.peek()
        if nxt is None or _indent_of(nxt) <= indent_gt:
            return None  # explicit null
        return _parse_block(cur, indent_gt)
    if val.startswith("{"):
        return _parse_flow_mapping(val)
    return _parse_scalar(val)


def _parse_block(cur, min_indent_exclusive):
    nxt = cur.peek()
    if nxt is None:
        return {}
    indent = _indent_of(nxt)
    if indent <= min_indent_exclusive:
        return {}
    content = nxt.strip()
    if content.startswith("- "):
        return _parse_sequence(cur, indent)
    return _parse_mapping(cur, indent)


def _parse_mapping(cur, indent):
    out = {}
    while True:
        line = cur.peek()
        if line is None or _indent_of(line) != indent:
            break
        content = line.strip()
        if content.startswith("- "):
            break
        kv = _KV_RE.match(content)
        if not kv:
            raise ValueError("miniyaml: expected 'key: value' at indent %d: %r" % (indent, line))
        cur.advance()
        key, rest = kv.group(1), kv.group(2)
        out[key] = _parse_value(rest, cur, indent)
    return out


def _parse_sequence(cur, indent):
    out = []
    while True:
        line = cur.peek()
        if line is None or _indent_of(line) != indent or not line.strip().startswith("- "):
            break
        cur.advance()
        item_body = line.strip()[2:]  # after "- "
        if item_body == "":
            # bare "-" then a nested block more indented than this dash
            out.append(_parse_block(cur, indent))
            continue
        kv = _KV_RE.match(item_body)
        if kv:
            # "- key: value" starts an inline mapping item; subsequent lines
            # indented past the dash (indent+2) continue that same mapping.
            item = {}
            key, rest = kv.group(1), kv.group(2)
            item_indent = indent + 2
            item[key] = _parse_value(rest, cur, indent)
            while True:
                nxt = cur.peek()
                if nxt is None or _indent_of(nxt) != item_indent:
                    break
                nxt_content = nxt.strip()
                if nxt_content.startswith("- "):
                    break
                kv2 = _KV_RE.match(nxt_content)
                if not kv2:
                    break
                cur.advance()
                item[kv2.group(1)] = _parse_value(kv2.group(2), cur, item_indent)
            out.append(item)
        else:
            out.append(_parse_scalar(item_body))
    return out


def load(text):
    lines = _strip_comment_and_blank_lines(text)
    cur = _Cursor(lines)
    return _parse_mapping(cur, 0)


def load_file(path):
    with open(path) as f:
        return load(f.read())
