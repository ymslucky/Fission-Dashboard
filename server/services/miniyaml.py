# -*- coding: utf-8 -*-
"""实用子集 YAML 解析/序列化（从旧 fn/ext1.py、ext6.py 移植，改为纯函数模块）。

支持：块级映射/序列、标量、引号字符串、内联 []/{}、| 与 |- 块标量、多文档 ---。
"""
from __future__ import annotations

import json
import re
from typing import Any


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s == "~" or s == "null":
        return None
    if s.startswith(("'", '"')):
        q = s[0]
        if len(s) >= 2 and s.endswith(q):
            return s[1:-1]
        return s
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_scalar(x) for x in re.split(r",(?![^\[]*\])", inner) if x.strip()] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out: dict = {}
        if inner:
            for part in re.split(r",(?![^{}]*\})", inner):
                k, _, v = part.partition(":")
                out[_parse_scalar(k)] = _parse_scalar(v)
        return out
    return s


def _strip_comment(line: str) -> str:
    out, in_q = "", None
    for ch in line:
        if in_q:
            out += ch
            if ch == in_q:
                in_q = None
        elif ch in ("'", '"'):
            in_q = ch
            out += ch
        elif ch == "#":
            break
        else:
            out += ch
    return out.rstrip()


def miniyaml_load(text: str) -> list:
    """解析为文档列表；单文档返回 [obj]。"""
    docs: list = []
    lines: list[tuple[str, int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "---":
            docs.append(None)
            lines.append(("DOC", 0, ""))
            continue
        lines.append(("L", len(stripped) - len(stripped.lstrip(" ")), stripped.strip()))

    has_doc = any(t == "DOC" for t, _, _ in lines)

    def parse_block(idx: int, indent: int):
        if idx >= len(lines) or lines[idx][0] == "DOC" or lines[idx][1] < indent:
            return {}, idx
        is_seq = lines[idx][0] == "L" and (lines[idx][2].startswith("- ") or lines[idx][2] == "-")
        if is_seq:
            arr: list = []
            while idx < len(lines) and lines[idx][0] == "L" and lines[idx][1] == indent and (
                lines[idx][2].startswith("- ") or lines[idx][2] == "-"
            ):
                item_line = lines[idx][2]
                if item_line == "-":
                    obj, idx = parse_block(idx + 1, indent + 2)
                    arr.append(obj)
                    continue
                rest = item_line[2:]
                if ":" in _strip_comment(rest) and not rest.startswith(("[", "{", '"', "'")):
                    k, _, v = rest.partition(":")
                    k = _parse_scalar(k)
                    v = _strip_comment(v).strip()
                    if v == "":
                        nxt_ind = lines[idx + 1][1] if idx + 1 < len(lines) and lines[idx + 1][1] > indent else indent + 2
                        obj, idx = parse_block(idx + 1, nxt_ind)
                        arr.append({k: obj})
                        continue
                    arr.append({k: _parse_scalar(v)})
                    idx += 1
                    continue
                arr.append(_parse_scalar(rest))
                idx += 1
            return arr, idx
        m: dict = {}
        while idx < len(lines) and lines[idx][0] == "L" and lines[idx][1] == indent and not lines[idx][2].startswith("- "):
            line = lines[idx][2]
            if ":" not in line:
                idx += 1
                continue
            k, _, v = line.partition(":")
            key = _parse_scalar(k)
            v = v.strip()
            if v in ("|", "|-", "|+", ">", ">-", ">+"):
                block_lines: list[str] = []
                idx += 1
                bind = None
                while idx < len(lines) and lines[idx][0] == "L" and (lines[idx][2] == "" or lines[idx][1] > indent):
                    if lines[idx][2] == "":
                        block_lines.append("")
                        idx += 1
                        continue
                    if bind is None:
                        bind = lines[idx][1]
                    block_lines.append(" " * (lines[idx][1] - bind) + lines[idx][2])
                    idx += 1
                m[key] = ("\n" if not v.startswith("|-") else "") + "\n".join(block_lines).rstrip("\n") + ("\n" if v in ("|", "|+") else "")
                continue
            if v == "":
                nxt = idx + 1
                if nxt < len(lines) and lines[nxt][0] == "L" and (lines[nxt][1] > indent or lines[nxt][0] == "DOC"):
                    if lines[nxt][0] == "DOC":
                        m[key] = None
                        idx = nxt
                        continue
                    obj, idx = parse_block(nxt, lines[nxt][1])
                    m[key] = obj
                    continue
                m[key] = None
                idx += 1
                continue
            m[key] = _parse_scalar(v)
            idx += 1
        return m, idx

    i = 0
    cur: dict = {}
    while i < len(lines):
        if lines[i][0] == "DOC":
            docs.append(cur)
            cur = {}
            i += 1
            continue
        obj, i = parse_block(i, lines[i][1])
        if isinstance(obj, dict):
            cur.update(obj)
    docs.append(cur)
    if not has_doc:
        return [docs[-1]] if docs else []
    return [d for d in docs if d]


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*!|>'\"%@`") or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    return s


def miniyaml_dump(obj: Any, indent: int = 0) -> str:
    pad = "  " * indent
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.append(miniyaml_dump(v, indent + 1))
            elif isinstance(v, (dict, list)):
                out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                first = True
                for k, v in item.items():
                    if isinstance(v, (dict, list)) and v:
                        prefix = f"{pad}- {k}:" if first else f"{pad}  {k}:"
                        out.append(prefix)
                        out.append(miniyaml_dump(v, indent + 2))
                    else:
                        out.append((f"{pad}- " if first else f"{pad}  ") + f"{k}: {_yaml_scalar(v)}")
                    first = False
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}")
    return "\n".join(out)
