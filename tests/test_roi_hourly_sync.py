from __future__ import annotations

from tools import roi_hourly_sync


def test_arg_parser_accepts_browser_meta_channel_for_systemd_compatibility():
    args = roi_hourly_sync.build_arg_parser().parse_args(["--meta-channel", "browser"])

    assert args.meta_channel == "browser"
