import json, pathlib, sys
here = pathlib.Path(__file__).parent
page = (here / "page.html").read_text(encoding="utf-8")
model = (here / "model.js").read_text(encoding="utf-8")
data = (here / "scenarios.json").read_text(encoding="utf-8")
out = page.replace("/*@MODEL@*/", model).replace("/*@DATA@*/", data)
(here / "duck-cell.html").write_text(out, encoding="utf-8")
print("wrote", len(out), "bytes")
