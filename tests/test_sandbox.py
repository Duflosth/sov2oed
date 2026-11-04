"""The rows script runs as data in, data out: refused before it runs when it reaches for the system."""

import pytest

from sov2oed import sandbox

LINES = [
    {
        "ref": "p1:l1",
        "page": 1,
        "line": 1,
        "text": "1 Hall 100",
        "words": [[10, 20, "1"], [30, 50, "Hall"], [60, 80, "100"]],
    }
]


def test_a_script_returns_rows_for_the_lines():
    script = "import re\ndef rows(lines):\n    return [['lines', 'n'], *[[x['ref'], re.sub(' .*', '', x['text'])] for x in lines]]\n"
    assert sandbox.run(script, LINES) == [["lines", "n"], ["p1:l1", "1"]]


@pytest.mark.parametrize(
    "script",
    [
        "import os\ndef rows(lines):\n    return os.listdir('.')",
        "def rows(lines):\n    return open('/etc/passwd').read()",
        "def rows(lines):\n    return ().__class__.__base__.__subclasses__()",
        "def rows(lines):\n    return __import__('subprocess')",
        "def other(lines):\n    return []",
    ],
)
def test_a_script_that_reaches_for_the_system_is_refused(script):
    with pytest.raises(ValueError, match="refused"):
        sandbox.run(script, LINES)


def test_an_import_hidden_from_the_tree_still_fails():
    script = "import json\ndef rows(lines):\n    m = json.loads('\"os\"')\n    return [[str(type(m))]]\n"
    assert sandbox.run(script, LINES) == [["<class 'str'>"]]
    script = "def rows(lines):\n    b = [v for k, v in dict(x=1).items()]\n    return [[str(b)]]\n"
    assert sandbox.run(script, LINES) == [["[1]"]]


def test_a_failing_script_says_why():
    with pytest.raises(ValueError, match="ZeroDivisionError"):
        sandbox.run("def rows(lines):\n    return 1 / 0\n", LINES)
