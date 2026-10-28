#!/usr/bin/env python3
"""Builds ../web/duck-orders.html from page.html, model.js and orders.json (python3 make_orders.py writes orders.json)."""
import pathlib
here = pathlib.Path(__file__).parent
page = (here / "page.html").read_text(encoding="utf-8")
out = page.replace("/*@MODEL@*/", (here / "model.js").read_text(encoding="utf-8")).replace("/*@DATA@*/", (here / "orders.json").read_text(encoding="utf-8"))
(here.parent / "web" / "duck-orders.html").write_text(out, encoding="utf-8")
print("wrote", len(out), "bytes")
