"""
build.py — inlines web/data.json into web/app.html to make one standalone file.

Plain English: the dashboard has to work as a single file you can double-click,
email, or publish, with no server and no second request. So the data is baked
straight into the HTML instead of being fetched.

    python web/export_data.py     # run both policies, write data.json
    python web/build.py           # inline it, write dashboard.html

`</script>` is escaped inside the JSON because an unescaped one would close the
tag it is sitting in and break the page.
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    with open(os.path.join(HERE, "app.html"), encoding="utf-8") as handle:
        template = handle.read()
    with open(os.path.join(HERE, "data.json"), encoding="utf-8") as handle:
        data = handle.read()

    if "__DATA__" not in template:
        raise SystemExit("app.html has no __DATA__ marker to fill")

    html = template.replace("__DATA__", data.replace("</", "<\\/"))

    out = os.path.join(HERE, "dashboard.html")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(html)

    print(f"wrote {out}  ({os.path.getsize(out) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
