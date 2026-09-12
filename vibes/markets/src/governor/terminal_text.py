"""Readable Markdown for streamed terminal replies, using Governor's accent palette."""

from rich.markdown import Markdown
from rich.text import Text
from rich.theme import Theme

REPLY_THEME = Theme(
    {
        "markdown.h1": "bold #ffffff",
        "markdown.h2": "bold #ffffff",
        "markdown.h3": "bold #ffffff",
        "markdown.h4": "bold #ffffff",
        "markdown.h5": "bold #ffffff",
        "markdown.h6": "bold #ffffff",
        "markdown.strong": "bold #ff994f",
        "markdown.em": "italic #b4e3a7",
        "markdown.code": "bold #b4e3a7 on #20271d",
        "markdown.link": "bold #ff994f",
        "markdown.link_url": "underline #ff994f",
        "markdown.item.bullet": "bold #ff994f",
        "markdown.item.number": "bold #ff994f",
        "markdown.block_quote": "#b4e3a7",
    }
)


class AssistantReply:
    def __init__(self, text):
        self.text = text

    def __rich_console__(self, console, options):
        label = Text("▸ ", style="bold #ff994f")
        label.append("CODEX", style="bold #ffffff")
        yield label
        yield Text("")
        # Scope styles to this reply; model text is Markdown, never terminal Rich markup.
        with console.use_theme(REPLY_THEME):
            yield from console.render(
                Markdown(self.text, style="#e6e3dc", code_theme="monokai"), options
            )
