import re
from pathlib import Path

from homely_bridge import __version__


def test_version_matches_config_yaml():
    config = (Path(__file__).parent.parent / "config.yaml").read_text()
    assert re.search(r'^version: "([^"]+)"', config, re.M).group(1) == __version__
