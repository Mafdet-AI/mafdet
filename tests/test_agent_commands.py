"""The agent's slash commands: the table, the menu above the message box, the answers as cards."""
import asyncio
import io

from rich.console import Console

from mafdet.agent import commands
from test_agent import Streams, tui_on, until


def ids(text):
    return [o.id for o in commands.matches(text)]


def test_matches_commands_then_their_values():
    assert ids("/") == [f"cmd:{c.name}" for c in commands.COMMANDS]
    assert ids("/re") == ["cmd:reasoning"]
    assert ids("/o") == [f"cmd:{c.name}" for c in commands.COMMANDS if "o" in c.name]    # none starts with o
    assert ids("/approve ") == ["arg:approve:ask", "arg:approve:edits", "arg:approve:all"]
    assert ids("/reasoning h") == ["arg:reasoning:high"]
    assert ids("/tools ") == [] and ids("hello /x") == [] and ids("/zz") == [] and ids("/new\nmore") == []


def test_find_aliases_and_suggestions():
    assert commands.find("/exit").name == "quit" and commands.find("help").name == "help"
    assert commands.suggest("tols") == "tools" and commands.suggest("xyzzy") == ""


def shown(app, cls):
    """The text of the widgets of a class, as the terminal shows it."""
    out = []
    for w in app.query(f".{cls}"):
        console = Console(width=118, record=True, file=io.StringIO())
        console.print(w.content)
        out.append(console.export_text())
    return out


def test_menu_completes_runs_and_closes(tmp_path):
    async def go():
        app = tui_on(tmp_path, Streams([]))
        async with app.run_test(size=(120, 40)) as pilot:
            menu = app.query_one("#slash")
            await pilot.press("/")
            await pilot.pause(0.1)
            assert menu.display and menu.option_count == len(commands.COMMANDS)
            await pilot.press(*"to")
            await pilot.pause(0.1)
            assert menu.option_count == 1
            await pilot.press("enter")                                          # runs /tools
            await until(pilot, lambda: shown(app, "card"))
            assert "Tools" in shown(app, "card")[-1] and "read_file" in shown(app, "card")[-1]
            assert not menu.display and app.query_one("#prompt").text == ""

            await pilot.press(*"/ap", "tab")                                    # Tab completes
            await pilot.pause(0.1)
            assert app.query_one("#prompt").text == "/approve " and menu.option_count == 3
            await pilot.press("down", "enter")                                  # edits
            await pilot.pause(0.1)
            assert app.mode == "edits" and "Approval: edits" in shown(app, "ok")[-1]

            await pilot.press(*"/he")
            await pilot.pause(0.1)
            await pilot.press("escape")                                         # closes the menu only
            await pilot.pause(0.1)
            assert not menu.display and app.query_one("#prompt").text == "/he"
    asyncio.run(go())


def test_unknown_commands_and_bad_values_say_what_to_do(tmp_path):
    async def go():
        app = tui_on(tmp_path, Streams([]))
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#prompt").submit("/tols")
            await pilot.pause(0.1)
            assert "There is no /tols." in shown(app, "ok")[-1] and "/tools" in shown(app, "ok")[-1]
            app.query_one("#prompt").submit("/approve maybe")
            await pilot.pause(0.1)
            assert "takes ask, edits, all" in shown(app, "ok")[-1] and app.mode == "ask"
            app.query_one("#prompt").submit("/help")
            await pilot.pause(0.1)
            card = shown(app, "card")[-1]
            assert all(f"/{c.name}" in card for c in commands.COMMANDS) and "Shift+Tab" in card
    asyncio.run(go())
