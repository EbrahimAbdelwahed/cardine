"""Used entry points from the verified firecrawl-anydoc 0.1.7 wheel's stubs.

The worker loads the bundled binary only after configuring its sandbox.
"""

from typing import Literal

Format = Literal[
    "doc", "docx", "odt", "pdf", "ppt", "pptx", "rtf", "epub", "xlsx", "ods", "odp", "csv"
]

def format_from_bytes(data: bytes | bytearray) -> Format | None: ...
def to_markdown_bytes(data: bytes | bytearray, format: Format | None = None) -> str: ...
