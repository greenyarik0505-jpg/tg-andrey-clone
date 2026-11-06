"""Inspect aggregate text statistics from a Telegram HTML export."""
import argparse
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


class MessageStats(HTMLParser):
    def __init__(self, top):
        super().__init__()
        self.top = top
        self.parts = None
        self.total = 0
        self.nonempty = 0
        self.words = 0
        self.frequency = Counter()

    def handle_starttag(self, tag, attrs):
        if tag == "td" and "c-text" in (dict(attrs).get("class") or "").split():
            self.parts = []
        elif tag == "br" and self.parts is not None:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != "td" or self.parts is None:
            return
        text = "".join(self.parts).strip()
        self.parts = None
        self.total += 1
        if text:
            self.nonempty += 1
            self.words += len(text.split())
            if self.top:
                self.frequency.update(re.findall(r"[^\W\d_]{3,}", text.casefold()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, nargs="?", help="Path to the HTML export")
    parser.add_argument("--top", type=int, default=0, help="Show this many frequent words")
    args = parser.parse_args()
    if args.top < 0:
        parser.error("--top must be non-negative")
    if args.export is None:
        candidates = list(Path.cwd().glob("SelfMessages_*.html"))
        if len(candidates) != 1:
            parser.error("Provide the path to one HTML export")
        args.export = candidates[0]

    stats = MessageStats(args.top)
    try:
        with args.export.open(encoding="utf-8-sig", errors="replace") as export:
            for block in iter(lambda: export.read(65536), ""):
                stats.feed(block)
        stats.close()
    except OSError as exc:
        parser.exit(1, f"Could not read the export: {exc.strerror}\n")

    average = stats.words / stats.nonempty if stats.nonempty else 0
    print(f"Messages: {stats.total}\nNon-empty messages: {stats.nonempty}\nAverage words: {average:.2f}")
    for word, count in stats.frequency.most_common(args.top):
        print(f"{word}: {count}")


if __name__ == "__main__":
    main()
